"""
Real-Time Arduino Serial Signal & Interpolation/Quantization Viewer (9600 baud, up to 5 s)

Features:
  - Automatic Arduino COM port detection
  - Live real-time plotting while recording up to 5 seconds at 9600 baud
  - Auto-detection of raw ADC (0–1023) vs Voltage (0–5 V) serial lines
  - Toggleable Quantization overlay (1–15 bits) + error fill
  - Toggleable PCHIP smooth interpolation (C1-smooth, overshoot-free)
  - Vector rendering (QPainter), per-axis zoom/pan, and SVG / PNG snapshots
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import serial
import serial.tools.list_ports
from PyQt6.QtCore import QPointF, QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from scipy.interpolate import PchipInterpolator

from adc_quantization_visualizer import QuantizationCanvas, quantize_signal

_NUM_RE = re.compile(r"(?<![A-Za-z_])[-+]?\d*\.?\d+")

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


def find_arduino_port() -> Tuple[Optional[str], str]:
    """Return (port_device, description) for the most likely Arduino COM port."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None, "No COM ports found"

    scored = []
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
        scored.append((score, p))

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_port = scored[0]
    if best_score < 0 and len(scored) > 1:
        return None, "No USB/Arduino COM port found"
    return best_port.device, best_port.description or best_port.device


def parse_serial_voltage(line: str, unit_mode: str = "auto") -> Optional[float]:
    """Parse a serial line into a 0–5 V float, or return None if unparseable."""
    match = _NUM_RE.search(line)
    if not match:
        return None
    val = float(match.group(0))
    if unit_mode == "raw":
        v = (val / 1023.0) * 5.0
    elif unit_mode == "volts":
        v = val
    else:
        v = (val / 1023.0) * 5.0 if val > 5.0 else val
    return float(np.clip(v, 0.0, 5.0))


def compute_pchip_interpolation(
    t: np.ndarray, v: np.ndarray, points_per_sample: int = 10
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute overshoot-free C1-smooth PCHIP interpolation over strictly increasing timestamps.
    """
    if len(t) < 2:
        return t.copy(), v.copy()

    # Ensure strictly increasing timestamps for PchipInterpolator
    keep = np.concatenate(([True], np.diff(t) > 1e-6))
    t_u = t[keep]
    v_u = v[keep]
    if len(t_u) < 2:
        return t_u, v_u

    n_dense = min(15000, max(200, len(t_u) * points_per_sample))
    t_dense = np.linspace(float(t_u[0]), float(t_u[-1]), n_dense)
    interp = PchipInterpolator(t_u, v_u)
    v_dense = np.clip(interp(t_dense), 0.0, 5.0)
    return t_dense, v_dense


class SerialRecorderThread(QThread):
    """Background thread that streams timestamped samples in real time at 9600 baud."""

    status_update = pyqtSignal(str)
    live_chunk = pyqtSignal(object, object)  # t_arr, v_arr
    finished_recording = pyqtSignal(object, object, str)
    recording_failed = pyqtSignal(str)

    def __init__(
        self,
        port: str,
        baud: int = 9600,
        max_duration_s: float = 5.0,
        unit_mode: str = "auto",
    ) -> None:
        super().__init__()
        self.port = port
        self.baud = baud
        self.max_duration_s = max_duration_s
        self.unit_mode = unit_mode
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        times: list[float] = []
        volts: list[float] = []

        try:
            with serial.Serial(self.port, self.baud, timeout=0.05) as ser:
                self.status_update.emit(
                    f"Connected to {self.port} @ {self.baud} baud (waiting for Arduino reset)..."
                )
                boot_wait_end = time.perf_counter() + 1.6
                while time.perf_counter() < boot_wait_end and not self._stop_requested:
                    time.sleep(0.05)
                ser.reset_input_buffer()

                t0 = time.perf_counter()
                last_ui = t0
                while not self._stop_requested:
                    elapsed = time.perf_counter() - t0
                    if elapsed >= self.max_duration_s:
                        break

                    raw_bytes = ser.readline()
                    if raw_bytes:
                        line = raw_bytes.decode("utf-8", errors="ignore").strip()
                        v = parse_serial_voltage(line, self.unit_mode)
                        if v is not None:
                            times.append(time.perf_counter() - t0)
                            volts.append(v)

                    now = time.perf_counter()
                    if now - last_ui >= 0.06:
                        if len(times) >= 2:
                            t_arr = np.asarray(times, dtype=np.float64)
                            t_arr -= t_arr[0]
                            v_arr = np.asarray(volts, dtype=np.float64)
                            self.live_chunk.emit(t_arr, v_arr)
                        self.status_update.emit(
                            f"Recording {self.port}: {now - t0:.1f} / {self.max_duration_s:.1f} s ({len(volts)} samples)..."
                        )
                        last_ui = now
        except Exception as exc:
            self.recording_failed.emit(f"Serial error on {self.port}: {exc}")
            return

        if len(volts) < 2:
            self.recording_failed.emit(
                f"Recorded only {len(volts)} valid sample(s) from {self.port}. "
                "Make sure the Arduino is printing numbers (e.g. Serial.println(analogRead(A0));) at 9600 baud."
            )
            return

        t_arr = np.asarray(times, dtype=np.float64)
        t_arr -= t_arr[0]
        if t_arr[-1] <= 0.0:
            t_arr = np.linspace(0.0, 0.1, len(volts))
        v_arr = np.asarray(volts, dtype=np.float64)
        self.finished_recording.emit(t_arr, v_arr, self.port)


class ArduinoCanvas(QuantizationCanvas):
    """Vector canvas supporting live Arduino data, optional quantization, and PCHIP interpolation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bits = 10
        self.show_quantization = False
        self.show_interpolation = True
        self.source_label = "Waiting for Arduino recording (press Record)"

        # Start with an empty flat baseline until first recording
        self.duration_s = 5.0
        self.t = np.array([0.0, 5.0], dtype=np.float64)
        self.v_orig = np.array([0.0, 0.0], dtype=np.float64)
        self.v_quant, self.bins = quantize_signal(self.v_orig, self.bits, self.V_MAX)
        self.t_interp, self.v_interp = self.t.copy(), self.v_orig.copy()
        self.x_min, self.x_max = 0.0, 5.0

    def set_recorded_data(
        self, t_arr: np.ndarray, v_arr: np.ndarray, source_label: str, fit_x: bool = False
    ) -> None:
        self.t = t_arr
        self.v_orig = v_arr
        self.source_label = source_label
        self.v_quant, self.bins = quantize_signal(v_arr, self.bits, self.V_MAX)
        if self.show_interpolation:
            self.t_interp, self.v_interp = compute_pchip_interpolation(t_arr, v_arr)
        else:
            self.t_interp, self.v_interp = t_arr, v_arr
        if fit_x and len(t_arr) >= 2 and t_arr[-1] > 0.0:
            self.duration_s = float(t_arr[-1])
            self.x_min = 0.0
            self.x_max = self.duration_s
        self.update()
        self._emit_stats()

    def set_show_quantization(self, show: bool) -> None:
        self.show_quantization = show
        self.update()

    def set_show_interpolation(self, show: bool) -> None:
        self.show_interpolation = show
        if show:
            self.t_interp, self.v_interp = compute_pchip_interpolation(self.t, self.v_orig)
        self.update()

    def render_to_painter(self, painter: QPainter, width: float, height: float) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        painter.fillRect(QRectF(0, 0, width, height), QColor("#FFFFFF"))
        rect = self._plot_rect(width, height)
        painter.fillRect(rect, QColor("#FAFCFF"))

        y_ticks = self._y_ticks()
        x_ticks = self._x_ticks()

        # Grid lines
        painter.setPen(QPen(QColor("#E4E9F2"), 1.0))
        for v, _, _ in y_ticks:
            py = self._data_to_px(self.x_min, v, rect).y()
            painter.drawLine(QPointF(rect.left(), py), QPointF(rect.right(), py))
        for xt, _ in x_ticks:
            px = self._data_to_px(xt, self.y_min, rect).x()
            painter.drawLine(QPointF(px, rect.top()), QPointF(px, rect.bottom()))

        painter.save()
        painter.setClipRect(rect)

        sl = self._visible_indices()
        t_vis = self.t[sl]
        v_vis = self.v_orig[sl]
        q_vis = self.v_quant[sl]

        if len(t_vis) >= 2:
            if self.show_quantization:
                # Light red sample-level error fill (only shades actual differences between v_orig and v_quant)
                poly = QPolygonF()
                for ti, vi in zip(t_vis, v_vis):
                    poly.append(self._data_to_px(float(ti), float(vi), rect))
                for ti, qi in zip(reversed(t_vis), reversed(q_vis)):
                    poly.append(self._data_to_px(float(ti), float(qi), rect))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(235, 65, 65, 60))
                painter.drawPolygon(poly)

            # Arduino recorded signal in blue
            blue_path = QPainterPath()
            blue_path.moveTo(self._data_to_px(float(t_vis[0]), float(v_vis[0]), rect))
            for ti, vi in zip(t_vis[1:], v_vis[1:]):
                blue_path.lineTo(self._data_to_px(float(ti), float(vi), rect))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#1D63DC"), 2.0))
            painter.drawPath(blue_path)

            # Optional PCHIP smooth interpolation in emerald green
            if self.show_interpolation and len(self.t_interp) >= 2:
                i0 = max(0, int(np.searchsorted(self.t_interp, self.x_min, side="left")) - 1)
                i1 = min(len(self.t_interp), int(np.searchsorted(self.t_interp, self.x_max, side="right")) + 1)
                ti_vis = self.t_interp[i0:i1]
                vi_vis = self.v_interp[i0:i1]
                if len(ti_vis) >= 2:
                    interp_path = QPainterPath()
                    interp_path.moveTo(self._data_to_px(float(ti_vis[0]), float(vi_vis[0]), rect))
                    for ti, vi in zip(ti_vis[1:], vi_vis[1:]):
                        interp_path.lineTo(self._data_to_px(float(ti), float(vi), rect))
                    painter.setPen(QPen(QColor("#0F9D58"), 2.0))
                    painter.drawPath(interp_path)

            # Optional quantization staircase in red
            if self.show_quantization:
                red_path = QPainterPath()
                red_path.moveTo(self._data_to_px(float(t_vis[0]), float(q_vis[0]), rect))
                for idx in range(1, len(t_vis)):
                    red_path.lineTo(self._data_to_px(float(t_vis[idx]), float(q_vis[idx - 1]), rect))
                    red_path.lineTo(self._data_to_px(float(t_vis[idx]), float(q_vis[idx]), rect))
                painter.setPen(QPen(QColor("#D92B2B"), 1.8))
                painter.drawPath(red_path)

        painter.restore()

        # Plot border
        painter.setPen(QPen(QColor("#334155"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        # Y-axis header & ticks
        mono_font = QFont("Consolas", 9)
        if not mono_font.exactMatch():
            mono_font = QFont("Courier New", 9)
        fm = QFontMetrics(mono_font)

        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor("#334155"))
        painter.drawText(
            QRectF(8, 10, rect.left() - 16, 24),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "Voltage | Bin | Binary",
        )

        painter.setFont(mono_font)
        for v, _, label in y_ticks:
            py = self._data_to_px(self.x_min, v, rect).y()
            painter.setPen(QPen(QColor("#334155"), 1.2))
            painter.drawLine(QPointF(rect.left() - 6, py), QPointF(rect.left(), py))
            painter.setPen(QColor("#1E293B"))
            painter.drawText(
                QRectF(6, py - fm.height() / 2 - 1, rect.left() - 14, fm.height() + 2),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        # X-axis ticks & label
        painter.setFont(QFont("Segoe UI", 9))
        for xt, xlabel in x_ticks:
            px = self._data_to_px(xt, self.y_min, rect).x()
            painter.setPen(QPen(QColor("#334155"), 1.2))
            painter.drawLine(QPointF(px, rect.bottom()), QPointF(px, rect.bottom() + 6))
            painter.setPen(QColor("#1E293B"))
            painter.drawText(
                QRectF(px - 45, rect.bottom() + 8, 90, 20),
                Qt.AlignmentFlag.AlignCenter,
                xlabel,
            )

        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(
            QRectF(rect.left(), rect.bottom() + 28, rect.width(), 22),
            Qt.AlignmentFlag.AlignCenter,
            "Time (seconds)",
        )

        # Header text
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.setPen(QColor("#0F172A"))
        header_txt = f"Arduino Signal ({self.source_label})"
        if self.show_quantization:
            stats = self.compute_visible_stats()
            header_txt += (
                f"  |  {self.bits}-bit Quant: Avg |Err| {stats['avg_abs']:.5f} V "
                f"({stats['avg_abs_bins']:.3f} bins)"
            )
        painter.drawText(
            QRectF(rect.left(), 8, rect.width(), 26),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            header_txt,
        )

        # Dynamic legend
        entries = [("Arduino Signal (0–5 V)", QColor("#1D63DC"), "line")]
        if self.show_interpolation:
            entries.append(("PCHIP Interpolation", QColor("#0F9D58"), "line"))
        if self.show_quantization:
            entries.append((f"Quantized ({self.bits}-bit)", QColor("#D92B2B"), "line"))
            entries.append(("Quantization Error", QColor(235, 65, 65, 90), "fill"))

        leg_w = 205
        leg_h = 12 + len(entries) * 18
        leg_rect = QRectF(rect.right() - leg_w - 10, rect.top() + 10, leg_w, leg_h)
        painter.setPen(QPen(QColor("#CBD5E1"), 1.0))
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawRoundedRect(leg_rect, 6, 6)

        painter.setFont(QFont("Segoe UI", 8))
        for idx, (lbl, col, kind) in enumerate(entries):
            y_l = leg_rect.top() + 15 + idx * 18
            if kind == "line":
                painter.setPen(QPen(col, 2.2))
                painter.drawLine(QPointF(leg_rect.left() + 10, y_l), QPointF(leg_rect.left() + 34, y_l))
            else:
                painter.setPen(QPen(QColor(235, 65, 65, 140), 1.0))
                painter.setBrush(col)
                painter.drawRect(QRectF(leg_rect.left() + 10, y_l - 6, 24, 11))
            painter.setPen(QColor("#1E293B"))
            painter.drawText(QPointF(leg_rect.left() + 42, y_l + 4), lbl)

    def export_svg(self, path: str) -> None:
        w = float(max(900, self.width()))
        h = float(max(560, self.height()))
        rect = self._plot_rect(w, h)
        y_ticks = self._y_ticks()
        x_ticks = self._x_ticks()

        sl = self._visible_indices()
        t_vis = self.t[sl]
        v_vis = self.v_orig[sl]
        q_vis = self.v_quant[sl]

        lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" '
            f'width="{w:.0f}" height="{h:.0f}">',
            f'  <rect width="{w:.0f}" height="{h:.0f}" fill="#FFFFFF"/>',
            f'  <defs><clipPath id="plot-clip"><rect x="{rect.left():.2f}" y="{rect.top():.2f}" '
            f'width="{rect.width():.2f}" height="{rect.height():.2f}"/></clipPath></defs>',
            f'  <rect x="{rect.left():.2f}" y="{rect.top():.2f}" width="{rect.width():.2f}" '
            f'height="{rect.height():.2f}" fill="#FAFCFF"/>',
        ]

        for v, _, _ in y_ticks:
            py = self._data_to_px(self.x_min, v, rect).y()
            lines.append(
                f'  <line x1="{rect.left():.2f}" y1="{py:.2f}" x2="{rect.right():.2f}" y2="{py:.2f}" '
                f'stroke="#E4E9F2" stroke-width="1"/>'
            )
        for xt, _ in x_ticks:
            px = self._data_to_px(xt, self.y_min, rect).x()
            lines.append(
                f'  <line x1="{px:.2f}" y1="{rect.top():.2f}" x2="{px:.2f}" y2="{rect.bottom():.2f}" '
                f'stroke="#E4E9F2" stroke-width="1"/>'
            )

        lines.append('  <g clip-path="url(#plot-clip)">')
        if len(t_vis) >= 2:
            if self.show_quantization:
                err_pts = []
                for ti, vi in zip(t_vis, v_vis):
                    p = self._data_to_px(float(ti), float(vi), rect)
                    err_pts.append(f"{p.x():.2f},{p.y():.2f}")
                for ti, qi in zip(reversed(t_vis), reversed(q_vis)):
                    p = self._data_to_px(float(ti), float(qi), rect)
                    err_pts.append(f"{p.x():.2f},{p.y():.2f}")
                lines.append(
                    f'    <polygon points="{" ".join(err_pts)}" fill="#EB4141" fill-opacity="0.24" stroke="none"/>'
                )

            blue_pts = [
                f"{p.x():.2f},{p.y():.2f}"
                for p in (self._data_to_px(float(ti), float(vi), rect) for ti, vi in zip(t_vis, v_vis))
            ]
            lines.append(
                f'    <polyline points="{" ".join(blue_pts)}" fill="none" stroke="#1D63DC" stroke-width="2"/>'
            )

            if self.show_interpolation and len(self.t_interp) >= 2:
                i0 = max(0, int(np.searchsorted(self.t_interp, self.x_min, side="left")) - 1)
                i1 = min(len(self.t_interp), int(np.searchsorted(self.t_interp, self.x_max, side="right")) + 1)
                ti_vis = self.t_interp[i0:i1]
                vi_vis = self.v_interp[i0:i1]
                if len(ti_vis) >= 2:
                    interp_pts = [
                        f"{p.x():.2f},{p.y():.2f}"
                        for p in (self._data_to_px(float(ti), float(vi), rect) for ti, vi in zip(ti_vis, vi_vis))
                    ]
                    lines.append(
                        f'    <polyline points="{" ".join(interp_pts)}" fill="none" stroke="#0F9D58" stroke-width="2"/>'
                    )

            if self.show_quantization:
                red_pts = []
                p0 = self._data_to_px(float(t_vis[0]), float(q_vis[0]), rect)
                red_pts.append(f"{p0.x():.2f},{p0.y():.2f}")
                for idx in range(1, len(t_vis)):
                    ph = self._data_to_px(float(t_vis[idx]), float(q_vis[idx - 1]), rect)
                    pv = self._data_to_px(float(t_vis[idx]), float(q_vis[idx]), rect)
                    red_pts.append(f"{ph.x():.2f},{ph.y():.2f}")
                    red_pts.append(f"{pv.x():.2f},{pv.y():.2f}")
                lines.append(
                    f'    <polyline points="{" ".join(red_pts)}" fill="none" stroke="#D92B2B" stroke-width="1.8"/>'
                )
        lines.append("  </g>")

        lines.append(
            f'  <rect x="{rect.left():.2f}" y="{rect.top():.2f}" width="{rect.width():.2f}" '
            f'height="{rect.height():.2f}" fill="none" stroke="#334155" stroke-width="1.5"/>'
        )
        lines.append(
            f'  <text x="{rect.left() - 10:.2f}" y="25" text-anchor="end" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="12" font-weight="bold" fill="#334155">'
            f'Voltage | Bin | Binary</text>'
        )
        for v, _, label in y_ticks:
            py = self._data_to_px(self.x_min, v, rect).y()
            lines.append(
                f'  <line x1="{rect.left() - 6:.2f}" y1="{py:.2f}" x2="{rect.left():.2f}" y2="{py:.2f}" '
                f'stroke="#334155" stroke-width="1.2"/>'
            )
            lines.append(
                f'  <text x="{rect.left() - 10:.2f}" y="{py + 4:.2f}" text-anchor="end" '
                f'font-family="Consolas, Courier New, monospace" font-size="11.5" fill="#1E293B">{label}</text>'
            )

        for xt, xlabel in x_ticks:
            px = self._data_to_px(xt, self.y_min, rect).x()
            lines.append(
                f'  <line x1="{px:.2f}" y1="{rect.bottom():.2f}" x2="{px:.2f}" y2="{rect.bottom() + 6:.2f}" '
                f'stroke="#334155" stroke-width="1.2"/>'
            )
            lines.append(
                f'  <text x="{px:.2f}" y="{rect.bottom() + 21:.2f}" text-anchor="middle" '
                f'font-family="Segoe UI, Arial, sans-serif" font-size="11.5" fill="#1E293B">{xlabel}</text>'
            )
        lines.append(
            f'  <text x="{rect.center().x():.2f}" y="{rect.bottom() + 43:.2f}" text-anchor="middle" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="13" font-weight="bold" fill="#1E293B">Time (seconds)</text>'
        )
        lines.append(
            f'  <text x="{rect.left():.2f}" y="25" text-anchor="start" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="13" font-weight="bold" fill="#0F172A">'
            f'Arduino Signal ({self.source_label})</text>'
        )
        lines.append("</svg>")
        Path(path).write_text("\n".join(lines), encoding="utf-8")


class ArduinoMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Arduino Serial Signal Viewer — Live Plot, Interpolation & Quantization")
        self.resize(1180, 730)
        self._recorder: Optional[SerialRecorderThread] = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # --- Row 1: Arduino Serial + Zoom/Export ---
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        row1.addWidget(QLabel("<b>Arduino (9600 baud):</b>"))
        self.port_label = QLabel()
        row1.addWidget(self.port_label)

        self.btn_refresh_port = QPushButton("Detect Port")
        row1.addWidget(self.btn_refresh_port)

        row1.addWidget(QLabel("<b>Duration (s):</b>"))
        self.rec_dur_spin = QDoubleSpinBox()
        self.rec_dur_spin.setRange(0.5, 5.0)
        self.rec_dur_spin.setSingleStep(0.5)
        self.rec_dur_spin.setDecimals(1)
        self.rec_dur_spin.setValue(5.0)
        row1.addWidget(self.rec_dur_spin)

        row1.addWidget(QLabel("<b>Units:</b>"))
        self.units_combo = QComboBox()
        self.units_combo.addItem("Auto (0–1023 or 0–5 V)", "auto")
        self.units_combo.addItem("Raw ADC (0–1023)", "raw")
        self.units_combo.addItem("Voltage (0–5 V)", "volts")
        row1.addWidget(self.units_combo)

        self.btn_record = QPushButton("Record from Arduino")
        self.btn_record.setStyleSheet("font-weight: bold; padding: 4px 10px;")
        row1.addWidget(self.btn_record)

        row1.addStretch()

        self.btn_x_in = QPushButton("X +")
        self.btn_x_out = QPushButton("X -")
        self.btn_y_in = QPushButton("Y +")
        self.btn_y_out = QPushButton("Y -")
        self.btn_zoom_in = QPushButton("XY +")
        self.btn_zoom_out = QPushButton("XY -")
        self.btn_reset = QPushButton("Reset")
        for b in (
            self.btn_x_in,
            self.btn_x_out,
            self.btn_y_in,
            self.btn_y_out,
            self.btn_zoom_in,
            self.btn_zoom_out,
            self.btn_reset,
        ):
            b.setMaximumWidth(52)
            row1.addWidget(b)

        self.btn_save_svg = QPushButton("Save SVG")
        self.btn_save_png = QPushButton("Save PNG")
        row1.addWidget(self.btn_save_svg)
        row1.addWidget(self.btn_save_png)
        root.addLayout(row1)

        # --- Row 2: Interpolation & Optional Quantization ---
        row2 = QHBoxLayout()
        row2.setSpacing(12)

        self.chk_interp = QCheckBox("Show Interpolation (PCHIP)")
        self.chk_interp.setChecked(True)
        row2.addWidget(self.chk_interp)

        self.chk_quant = QCheckBox("Show Quantization")
        self.chk_quant.setChecked(False)
        row2.addWidget(self.chk_quant)

        row2.addWidget(QLabel("<b>Bits:</b>"))
        self.bits_slider = QSlider(Qt.Orientation.Horizontal)
        self.bits_slider.setRange(1, 15)
        self.bits_slider.setValue(10)
        self.bits_slider.setFixedWidth(140)
        row2.addWidget(self.bits_slider)

        self.bits_spin = QSpinBox()
        self.bits_spin.setRange(1, 15)
        self.bits_spin.setValue(10)
        row2.addWidget(self.bits_spin)

        self.bins_label = QLabel()
        row2.addWidget(self.bins_label)

        row2.addStretch()
        self.rec_status_label = QLabel("Ready — press Record from Arduino")
        self.rec_status_label.setStyleSheet("color: #334155;")
        row2.addWidget(self.rec_status_label)
        root.addLayout(row2)

        # --- Canvas ---
        self.canvas = ArduinoCanvas()
        root.addWidget(self.canvas, stretch=1)

        # --- Bottom Status Bar ---
        stats_row = QHBoxLayout()
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("font-size: 13px; padding: 4px;")
        stats_row.addWidget(self.stats_label)
        stats_row.addStretch()
        help_lbl = QLabel("Wheel: Zoom (over axis or Shift=X, Ctrl=Y)  |  Left-Drag: Pan")
        help_lbl.setStyleSheet("color: #475569; font-size: 12px;")
        stats_row.addWidget(help_lbl)
        root.addLayout(stats_row)

        # --- Signals ---
        self.canvas.on_stats_changed = self._update_stats_label
        self.btn_refresh_port.clicked.connect(self._refresh_port)
        self.btn_record.clicked.connect(self._toggle_record)
        self.chk_interp.toggled.connect(self.canvas.set_show_interpolation)
        self.chk_quant.toggled.connect(self._on_quant_toggled)
        self.bits_slider.valueChanged.connect(self._on_bits_changed)
        self.bits_spin.valueChanged.connect(self._on_bits_changed)

        self.btn_x_in.clicked.connect(lambda: self.canvas.zoom_by(0.75, zoom_x=True, zoom_y=False))
        self.btn_x_out.clicked.connect(lambda: self.canvas.zoom_by(1.33, zoom_x=True, zoom_y=False))
        self.btn_y_in.clicked.connect(lambda: self.canvas.zoom_by(0.75, zoom_x=False, zoom_y=True))
        self.btn_y_out.clicked.connect(lambda: self.canvas.zoom_by(1.33, zoom_x=False, zoom_y=True))
        self.btn_zoom_in.clicked.connect(lambda: self.canvas.zoom_by(0.75))
        self.btn_zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1.33))
        self.btn_reset.clicked.connect(self.canvas.reset_view)
        self.btn_save_svg.clicked.connect(self._save_svg)
        self.btn_save_png.clicked.connect(self._save_png)

        self._on_quant_toggled(False)
        self._update_bins_label(10)
        self._update_stats_label(self.canvas.compute_visible_stats())
        self._refresh_port()

    def _refresh_port(self) -> Optional[str]:
        port, desc = find_arduino_port()
        if port:
            self.port_label.setText(f"<span style='color:#007A33;'><b>{port}</b></span> ({desc})")
        else:
            self.port_label.setText("<span style='color:#B91C1C;'>No Arduino found</span>")
        return port

    def _on_quant_toggled(self, checked: bool) -> None:
        self.bits_slider.setEnabled(checked)
        self.bits_spin.setEnabled(checked)
        self.bins_label.setEnabled(checked)
        self.canvas.set_show_quantization(checked)
        self._update_stats_label(self.canvas.compute_visible_stats())

    def _on_bits_changed(self, value: int) -> None:
        if self.bits_slider.value() != value:
            self.bits_slider.setValue(value)
        if self.bits_spin.value() != value:
            self.bits_spin.setValue(value)
        self._update_bins_label(value)
        self.canvas.set_bits(value)

    def _update_bins_label(self, bits: int) -> None:
        n_bins = 1 << bits
        step_mv = (5.0 / (n_bins - 1)) * 1000.0
        self.bins_label.setText(f"({n_bins:,} bins | ΔV = {step_mv:.3f} mV)")

    def _toggle_record(self) -> None:
        if self._recorder is not None and self._recorder.isRunning():
            self._recorder.request_stop()
            self.btn_record.setEnabled(False)
            self.rec_status_label.setText("Stopping recording...")
            return

        port = self._refresh_port()
        if not port:
            QMessageBox.warning(
                self,
                "Arduino Not Found",
                "Could not find an Arduino COM port.\n\n"
                "Check the USB cable and make sure Arduino IDE Serial Monitor is closed.",
            )
            return

        max_dur = float(self.rec_dur_spin.value())
        unit_mode = str(self.units_combo.currentData())

        # Pre-set X window to [0, max_dur] for smooth real-time sweep
        self.canvas.duration_s = max_dur
        self.canvas.x_min = 0.0
        self.canvas.x_max = max_dur
        self.canvas.y_min = -0.15
        self.canvas.y_max = 5.15

        self._recorder = SerialRecorderThread(port, baud=9600, max_duration_s=max_dur, unit_mode=unit_mode)
        self._recorder.status_update.connect(self.rec_status_label.setText)
        self._recorder.live_chunk.connect(self._on_live_chunk)
        self._recorder.finished_recording.connect(self._on_recording_finished)
        self._recorder.recording_failed.connect(self._on_recording_failed)
        self.btn_record.setText("Stop Recording")
        self.btn_refresh_port.setEnabled(False)
        self._recorder.start()

    def _on_live_chunk(self, t_arr: np.ndarray, v_arr: np.ndarray) -> None:
        self.canvas.set_recorded_data(t_arr, v_arr, f"Live {self._recorder.port}", fit_x=False)

    def _on_recording_finished(self, t_arr: np.ndarray, v_arr: np.ndarray, port_name: str) -> None:
        self.canvas.set_recorded_data(
            t_arr, v_arr, f"{port_name}, {len(v_arr)} samples, {t_arr[-1]:.2f} s", fit_x=True
        )
        self.btn_record.setText("Record from Arduino")
        self.btn_record.setEnabled(True)
        self.btn_refresh_port.setEnabled(True)
        self.rec_status_label.setText(
            f"Recorded from {port_name} ({len(v_arr)} samples, {t_arr[-1]:.2f} s)"
        )

    def _on_recording_failed(self, msg: str) -> None:
        self.btn_record.setText("Record from Arduino")
        self.btn_record.setEnabled(True)
        self.btn_refresh_port.setEnabled(True)
        self.rec_status_label.setText("Recording failed")
        QMessageBox.warning(self, "Arduino Recording Error", msg)

    def _update_stats_label(self, stats: dict) -> None:
        if not self.canvas.show_quantization:
            self.stats_label.setText(
                f"<b>Visible Window ({stats['count']} samples):</b>  "
                f"Quantization overlay hidden (toggle <i>Show Quantization</i> to compare)."
            )
            return
        self.stats_label.setText(
            f"<b>Visible Window ({stats['count']} samples):</b>  "
            f"Average |Error| = <b>{stats['avg_abs']:.6f} V</b> ({stats['avg_abs']*1000:.3f} mV | <b>{stats['avg_abs_bins']:.3f} bins</b>)  |  "
            f"Sum |Error| = <b>{stats['sum_abs']:.4f} V</b> (<b>{stats['sum_abs_bins']:.2f} bins</b>)"
        )

    def _save_svg(self) -> None:
        default_name = "arduino_signal.svg"
        path, _ = QFileDialog.getSaveFileName(self, "Save Vector SVG Snapshot", default_name, "SVG Files (*.svg)")
        if path:
            self.canvas.export_svg(path)

    def _save_png(self) -> None:
        default_name = "arduino_signal.png"
        path, _ = QFileDialog.getSaveFileName(self, "Save PNG Snapshot", default_name, "PNG Files (*.png)")
        if path:
            self.canvas.export_png(path, scale=2.0)


def main() -> None:
    app = QApplication(sys.argv)
    win = ArduinoMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
