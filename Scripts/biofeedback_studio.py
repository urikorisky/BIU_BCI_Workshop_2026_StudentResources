"""Launcher for the Real-Time Biofeedback Studio (GUI or zero-GUI headless mode)."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Real-Time Biofeedback Studio (GUI or headless LSL processor/broadcaster)."
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run DSP + LSL broadcasting without any Qt GUI.",
    )
    parser.add_argument(
        "--stream",
        default=None,
        help="Input LSL stream name (partial match) or 'synthetic'. Headless defaults to 'synthetic'.",
    )
    parser.add_argument(
        "--ecg-ch",
        type=int,
        default=0,
        help="0-based ECG channel index (-1 for none; default: 0).",
    )
    parser.add_argument(
        "--eda-ch",
        type=int,
        default=1,
        help="0-based EDA channel index (-1 for none; default: 1).",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=None,
        help="Optional calibration JSON path to load before starting.",
    )
    parser.add_argument(
        "--record",
        type=Path,
        default=None,
        help="Record raw, filtered, features, and events LSL streams via separate process into this folder.",
    )
    parser.add_argument(
        "--no-signals",
        action="store_true",
        help="Start GUI with signal waveform visualization disabled.",
    )
    parser.add_argument(
        "--no-features",
        action="store_true",
        help="Start GUI with feature metric & template visualization disabled.",
    )
    parser.add_argument(
        "--no-lsl",
        action="store_true",
        help="Disable outgoing BiofeedbackFeatures / BiofeedbackEvents LSL streams.",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="Automatically start streaming after launching the GUI.",
    )
    return parser.parse_args(argv)


def _resolve_headless_source(stream_name: str | None) -> tuple[object, float, str]:
    from biofeedback.lsl_io import LSLInputSource, SyntheticBioSource, discover_lsl_streams

    target = (stream_name or "synthetic").strip()
    if target.lower() == "synthetic":
        return SyntheticBioSource(fs=250.0), 250.0, "SyntheticBioSignals"

    deadline = time.monotonic() + 3.0
    while True:
        for meta in discover_lsl_streams(timeout_s=0.8):
            if target.lower() in meta["name"].lower():
                fs = meta["nominal_srate"] if meta["nominal_srate"] > 0 else 250.0
                return LSLInputSource(meta["info"]), float(fs), str(meta["name"])
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    raise RuntimeError(f"Could not find LSL input stream matching {target!r}")


def _spawn_recorder(out_dir: Path, raw_stream_name: str) -> subprocess.Popen[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    recorder_script = SCRIPT_DIR / "biofeedback_recorder.py"
    return subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(recorder_script),
            "--out-dir",
            str(out_dir),
            "--raw-stream",
            raw_stream_name,
            "--stdin-stop",
        ],
        stdin=subprocess.PIPE,
        text=True,
    )


def _stop_recorder(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    try:
        if proc.stdin is not None:
            try:
                proc.stdin.write("STOP\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.stdin.close()
            except Exception:
                pass
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=3.0)


def _run_headless(args: argparse.Namespace) -> int:
    from biofeedback.engine import BiofeedbackEngine

    engine = BiofeedbackEngine()
    engine.set_record_display_buffers(False)

    if args.calibration is not None:
        engine.load_calibration(args.calibration)

    try:
        source, fs, resolved_name = _resolve_headless_source(args.stream)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1

    broadcast = (not args.no_lsl) or (args.record is not None)
    engine.start(
        source,  # type: ignore[arg-type]
        fs=fs,
        ecg_ch_idx=args.ecg_ch,
        eda_ch_idx=args.eda_ch,
        broadcast_lsl=broadcast,
        start_thread=True,
    )
    rec_proc = _spawn_recorder(args.record, resolved_name) if args.record is not None else None
    print(
        f"Headless Biofeedback Studio running | stream={args.stream or 'synthetic'} "
        f"@ {fs:.0f} Hz | LSL={'ON' if broadcast else 'OFF'}"
        + (f" | REC={args.record}" if args.record is not None else "")
        + " (Ctrl+C to stop)",
        flush=True,
    )
    try:
        while engine.running:
            time.sleep(2.0)
            snap, _, t_latest, _, _, _, _, n_samples = engine.get_feature_render_data()
            print(
                f"[t={t_latest:6.1f}s | samples={n_samples}] "
                f"BPM={snap.bpm_rolling:5.1f} | EDA_tonic={snap.eda_tonic:6.2f} | "
                f"EDA_phasic={snap.eda_phasic:+6.2f} | SCR/min={snap.eda_scr_rate_per_min:4.1f}",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\nStopping headless Biofeedback Studio...", flush=True)
    finally:
        _stop_recorder(rec_proc)
        engine.stop()

    if engine.last_error:
        print(f"Engine error: {engine.last_error}", file=sys.stderr, flush=True)
        return 1
    return 0


def _run_gui(args: argparse.Namespace) -> int:
    from PyQt6.QtWidgets import QApplication

    from biofeedback.main_window import BiofeedbackWindow

    app = QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    win = BiofeedbackWindow(
        show_signals=not args.no_signals,
        show_features=not args.no_features,
    )
    if args.no_lsl and args.record is None:
        win.lsl_out_cb.setChecked(False)
    if args.calibration is not None:
        win.load_calibration_from_path(args.calibration)
    if args.stream is not None:
        win.select_stream_by_name(args.stream, refresh=True)
    if args.ecg_ch != 0 or args.eda_ch != 1:
        win.select_channels(args.ecg_ch, args.eda_ch)
    if args.auto_start:
        win.start_stream()
        if args.record is not None:
            win.start_recording(args.record)

    win.show()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.headless:
        return _run_headless(args)
    return _run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
