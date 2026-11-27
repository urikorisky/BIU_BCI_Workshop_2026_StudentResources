"""
Arduino Serial (ASCII) -> LabStreamingLayer (LSL) Bridge (GUI)

Reads numeric values (single-channel or comma/space-separated multi-channel)
from an Arduino COM port and publishes them as a clean float32 LSL stream
for LabRecorder (.xdf) and Python LSL consumers.

Requirements:
    pip install pyserial pylsl
"""

from __future__ import annotations

import argparse
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional, Tuple

try:
    import serial
    import serial.tools.list_ports
except ImportError as exc:
    raise SystemExit("Missing 'pyserial'. Install with: pip install pyserial") from exc

try:
    from pylsl import StreamInfo, StreamOutlet, cf_float32, local_clock
except ImportError as exc:
    raise SystemExit("Missing 'pylsl'. Install with: pip install pylsl") from exc

_NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")

_ARDUINO_HINTS = (
    "arduino",
    "ch340",
    "cp210",
    "ftdi",
    "usb serial",
    "usb-serial",
    "uno",
    "mega",
    "nano",
)
_ARDUINO_VIDS = ("2341", "2A03", "1A86", "10C4", "0403")


def list_serial_ports() -> List[Tuple[str, str, int]]:
    """Return list of (device, description, score) sorted by Arduino likelihood."""
    ports = list(serial.tools.list_ports.comports())
    scored: List[Tuple[str, str, int]] = []
    for p in ports:
        desc = (p.description or "").lower()
        mfg = (p.manufacturer or "").lower()
        hwid = (p.hwid or "").upper()
        score = 0
        if any(h in desc or h in mfg for h in _ARDUINO_HINTS):
            score += 10
        if any(f"VID_{v}" in hwid or f"VID:{v}" in hwid for v in _ARDUINO_VIDS):
            score += 8
        if "bluetooth" in desc:
            score -= 20
        scored.append((p.device, p.description or p.device, score))
    scored.sort(key=lambda item: item[2], reverse=True)
    return scored


def parse_numeric_row(line: str) -> Optional[List[float]]:
    """Extract all numbers from a serial line, or return None if none found."""
    matches = _NUM_RE.findall(line)
    if not matches:
        return None
    return [float(m) for m in matches]


def build_outlet(
    name: str,
    stype: str,
    n_channels: int,
    srate: float,
    labels: Optional[List[str]],
    port: str,
) -> StreamOutlet:
    """Create an LSL StreamOutlet with channel metadata for LabRecorder / pyxdf."""
    source_id = f"arduino_{port}_{name}".replace(" ", "_")
    info = StreamInfo(
        name=name,
        type=stype,
        channel_count=n_channels,
        nominal_srate=srate,
        channel_format=cf_float32,
        source_id=source_id,
    )
    chns = info.desc().append_child("channels")
    for i in range(n_channels):
        lbl = labels[i] if labels and i < len(labels) else f"Ch{i + 1}"
        ch = chns.append_child("channel")
        ch.append_child_value("label", lbl)
        ch.append_child_value("unit", "raw")
        ch.append_child_value("type", stype)
    return StreamOutlet(info)


class SerialToLSLApp:
    def __init__(self, root: tk.Tk, defaults: argparse.Namespace) -> None:
        self.root = root
        self.root.title("Arduino Serial -> LSL Bridge")
        self.root.geometry("620x470")
        self.root.minsize(560, 430)

        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._ui_queue: queue.Queue[Tuple[str, object]] = queue.Queue()
        self._port_map: dict[str, str] = {}

        self._build_ui(defaults)
        self._refresh_ports(preferred=defaults.port)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_queue)

    def _build_ui(self, defaults: argparse.Namespace) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        # --- Serial Frame ---
        ser_frame = ttk.LabelFrame(main, text="Arduino Serial Settings", padding=10)
        ser_frame.pack(fill=tk.X, pady=(0, 8))
        ser_frame.columnconfigure(1, weight=1)

        ttk.Label(ser_frame, text="COM Port:").grid(row=0, column=0, sticky=tk.W, **pad)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(ser_frame, textvariable=self.port_var, state="readonly")
        self.port_combo.grid(row=0, column=1, sticky=tk.EW, **pad)
        self.refresh_btn = ttk.Button(ser_frame, text="Refresh", command=self._refresh_ports)
        self.refresh_btn.grid(row=0, column=2, **pad)

        ttk.Label(ser_frame, text="Baud Rate:").grid(row=1, column=0, sticky=tk.W, **pad)
        self.baud_var = tk.StringVar(value=str(defaults.baud))
        self.baud_combo = ttk.Combobox(
            ser_frame,
            textvariable=self.baud_var,
            values=["9600", "19200", "38400", "57600", "115200"],
            state="readonly",
            width=14,
        )
        self.baud_combo.grid(row=1, column=1, sticky=tk.W, **pad)

        # --- LSL Frame ---
        lsl_frame = ttk.LabelFrame(main, text="LSL Stream Settings", padding=10)
        lsl_frame.pack(fill=tk.X, pady=(0, 8))
        lsl_frame.columnconfigure(1, weight=1)
        lsl_frame.columnconfigure(3, weight=1)

        ttk.Label(lsl_frame, text="Stream Name:").grid(row=0, column=0, sticky=tk.W, **pad)
        self.name_var = tk.StringVar(value=defaults.name)
        ttk.Entry(lsl_frame, textvariable=self.name_var).grid(row=0, column=1, sticky=tk.EW, **pad)

        ttk.Label(lsl_frame, text="Stream Type:").grid(row=0, column=2, sticky=tk.W, **pad)
        self.type_var = tk.StringVar(value=defaults.stype)
        ttk.Entry(lsl_frame, textvariable=self.type_var).grid(row=0, column=3, sticky=tk.EW, **pad)

        ttk.Label(lsl_frame, text="Channels:").grid(row=1, column=0, sticky=tk.W, **pad)
        self.channels_var = tk.StringVar(value=defaults.channels)
        ttk.Combobox(
            lsl_frame,
            textvariable=self.channels_var,
            values=["1", "2", "3", "4", "Auto"],
            state="readonly",
            width=10,
        ).grid(row=1, column=1, sticky=tk.W, **pad)

        ttk.Label(lsl_frame, text="Nominal Hz:").grid(row=1, column=2, sticky=tk.W, **pad)
        self.srate_var = tk.StringVar(value=str(defaults.srate))
        ttk.Entry(lsl_frame, textvariable=self.srate_var, width=10).grid(row=1, column=3, sticky=tk.W, **pad)

        ttk.Label(lsl_frame, text="Channel Labels:").grid(row=2, column=0, sticky=tk.W, **pad)
        self.labels_var = tk.StringVar(value=defaults.labels)
        ttk.Entry(lsl_frame, textvariable=self.labels_var).grid(
            row=2, column=1, columnspan=3, sticky=tk.EW, **pad
        )

        # --- Controls & Status ---
        ctrl = ttk.Frame(main)
        ctrl.pack(fill=tk.X, pady=(0, 8))

        self.start_btn = ttk.Button(ctrl, text="Start LSL Stream", command=self._toggle_stream)
        self.start_btn.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Idle — select COM port and click Start.")
        ttk.Label(ctrl, textvariable=self.status_var, font=("Segoe UI", 9, "bold")).pack(
            side=tk.LEFT, padx=(12, 0)
        )

        # --- Live Monitor ---
        mon_frame = ttk.LabelFrame(main, text="Live Monitor (Latest Value & Raw Serial)", padding=8)
        mon_frame.pack(fill=tk.BOTH, expand=True)

        self.latest_var = tk.StringVar(value="Latest LSL sample: —")
        ttk.Label(mon_frame, textvariable=self.latest_var, font=("Consolas", 11, "bold")).pack(
            anchor=tk.W, pady=(0, 6)
        )

        self.log_text = tk.Text(
            mon_frame,
            height=8,
            state=tk.DISABLED,
            font=("Consolas", 9),
            bg="#0f172a",
            fg="#e2e8f0",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _refresh_ports(self, preferred: str = "") -> None:
        ports = list_serial_ports()
        self._port_map.clear()
        display_items: List[str] = []
        selected_idx = 0

        for idx, (dev, desc, _) in enumerate(ports):
            label = f"{dev} — {desc}"
            self._port_map[label] = dev
            display_items.append(label)
            if preferred and dev.upper() == preferred.strip().upper():
                selected_idx = idx

        self.port_combo["values"] = display_items
        if display_items:
            self.port_combo.current(selected_idx)
        else:
            self.port_var.set("")

    def _append_log(self, msg: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 120:
            self.log_text.delete("1.0", f"{lines - 120}.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _toggle_stream(self) -> None:
        if self._worker and self._worker.is_alive():
            self._stop_stream()
        else:
            self._start_stream()

    def _start_stream(self) -> None:
        port_label = self.port_var.get().strip()
        port = self._port_map.get(port_label, port_label)
        if not port:
            messagebox.showwarning("No COM Port", "No serial COM port selected. Connect Arduino and click Refresh.")
            return

        try:
            baud = int(self.baud_var.get())
            srate = float(self.srate_var.get() or "0")
        except ValueError:
            messagebox.showerror("Invalid Number", "Baud rate and Nominal Hz must be valid numbers.")
            return

        name = self.name_var.get().strip() or "ArduinoBioSignals"
        stype = self.type_var.get().strip() or "BioSignals"
        ch_str = self.channels_var.get().strip()
        fixed_ch = 0 if ch_str == "Auto" else int(ch_str)
        labels = [s.strip() for s in self.labels_var.get().split(",") if s.strip()] or None

        self._stop_event.clear()
        self.start_btn.configure(text="Stop LSL Stream")
        self.refresh_btn.configure(state=tk.DISABLED)
        self.status_var.set(f"Opening {port} @ {baud} baud...")
        self._append_log(f"--- Connecting to {port} @ {baud} baud ---")

        self._worker = threading.Thread(
            target=self._stream_loop,
            args=(port, baud, name, stype, fixed_ch, srate, labels),
            daemon=True,
        )
        self._worker.start()

    def _stop_stream(self) -> None:
        self._stop_event.set()
        self.start_btn.configure(text="Start LSL Stream")
        self.refresh_btn.configure(state=tk.NORMAL)
        self.status_var.set("Stopped.")

    def _stream_loop(
        self,
        port: str,
        baud: int,
        name: str,
        stype: str,
        fixed_ch: int,
        srate: float,
        labels: Optional[List[str]],
    ) -> None:
        ser: Optional[serial.Serial] = None
        outlet: Optional[StreamOutlet] = None
        try:
            ser = serial.Serial(port, baud, timeout=0.5)
            time.sleep(1.5)
            ser.reset_input_buffer()

            if fixed_ch > 0:
                outlet = build_outlet(name, stype, fixed_ch, srate, labels, port)
                self._ui_queue.put(("status", f"LSL active ({name}, {fixed_ch} ch) — waiting for data..."))
                self._ui_queue.put(
                    ("log", f"LSL outlet created immediately: name={name!r}, type={stype!r}, ch={fixed_ch}")
                )
            else:
                self._ui_queue.put(("status", "Connected — waiting for first numeric line..."))

            expected_ch = fixed_ch
            count = 0
            t0 = time.monotonic()
            last_ui = 0.0

            while not self._stop_event.is_set():
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                row = parse_numeric_row(line)
                now = time.monotonic()
                if not row:
                    if now - last_ui > 0.25:
                        self._ui_queue.put(("log", f"[non-numeric] {line}"))
                        last_ui = now
                    continue

                if outlet is None:
                    expected_ch = len(row)
                    outlet = build_outlet(name, stype, expected_ch, srate, labels, port)
                    self._ui_queue.put(
                        ("log", f"LSL outlet started (Auto={expected_ch} ch): name={name!r}, type={stype!r}")
                    )

                if len(row) != expected_ch:
                    if now - last_ui > 0.5:
                        self._ui_queue.put(
                            ("log", f"[skipped {len(row)} vals != {expected_ch} ch] {line}")
                        )
                        last_ui = now
                    continue

                outlet.push_sample(row, timestamp=local_clock())
                count += 1

                if now - last_ui >= 0.15:
                    hz = count / max(now - t0, 1e-6)
                    self._ui_queue.put(("sample", (row, count, hz, line)))
                    last_ui = now

        except Exception as exc:
            self._ui_queue.put(("error", str(exc)))
        finally:
            if ser is not None and ser.is_open:
                ser.close()
            del outlet
            self._ui_queue.put(("stopped", None))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "log":
                    self._append_log(str(payload))
                elif kind == "sample":
                    row, count, hz, line = payload  # type: ignore[misc]
                    self.latest_var.set(f"Latest LSL sample: {row}")
                    self.status_var.set(f"Streaming to LSL: {count} samples (~{hz:.1f} Hz)")
                    self._append_log(f"{line}  ->  {row}")
                elif kind == "error":
                    self._append_log(f"[ERROR] {payload}")
                    messagebox.showerror("Serial / LSL Error", str(payload))
                elif kind == "stopped":
                    self.start_btn.configure(text="Start LSL Stream")
                    self.refresh_btn.configure(state=tk.NORMAL)
                    if not self.status_var.get().startswith("Idle"):
                        self.status_var.set("Stopped.")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_close(self) -> None:
        self._stop_event.set()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="GUI bridge: Arduino ASCII Serial -> LSL.")
    parser.add_argument("--port", default="", help="Initial serial port (e.g. COM10).")
    parser.add_argument("--baud", type=int, default=9600, help="Initial baud rate (default: 9600).")
    parser.add_argument("--name", default="ArduinoBioSignals", help="LSL stream name.")
    parser.add_argument("--type", dest="stype", default="BioSignals", help="LSL stream type.")
    parser.add_argument("--channels", default="1", help="Channel count (1..4 or Auto).")
    parser.add_argument("--srate", type=float, default=0.0, help="Nominal sample rate Hz (0 = irregular).")
    parser.add_argument("--labels", default="", help="Comma-separated channel labels (e.g. ECG,EDA).")
    args = parser.parse_args()

    root = tk.Tk()
    SerialToLSLApp(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
