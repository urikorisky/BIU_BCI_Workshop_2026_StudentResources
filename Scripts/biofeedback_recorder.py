"""Standalone LSL recorder for incoming raw, filtered, feature, and event streams."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from biofeedback.recorder import LSLSessionRecorder


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record Biofeedback Studio LSL streams (raw, filtered, features, events) to CSV."
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Destination folder for CSV files (default: Recordings/biofeedback_rec_<timestamp>).",
    )
    parser.add_argument(
        "--raw-stream",
        default=None,
        help="Incoming raw LSL stream name (partial match) or 'auto' (default: auto).",
    )
    parser.add_argument(
        "--filtered-stream",
        default="BiofeedbackFiltered",
        help="Filtered output LSL stream name (default: BiofeedbackFiltered).",
    )
    parser.add_argument(
        "--features-stream",
        default="BiofeedbackFeatures",
        help="Features LSL stream name (default: BiofeedbackFeatures).",
    )
    parser.add_argument(
        "--events-stream",
        default="BiofeedbackEvents",
        help="Events LSL stream name (default: BiofeedbackEvents).",
    )
    parser.add_argument(
        "--resolve-timeout",
        type=float,
        default=6.0,
        help="Seconds to wait when discovering LSL streams (default: 6.0).",
    )
    parser.add_argument(
        "--stdin-stop",
        action="store_true",
        help="Stop cleanly when 'STOP' is written to stdin or stdin closes.",
    )
    parser.add_argument(
        "--json-progress",
        action="store_true",
        help="Print periodic REC_STATUS JSON status lines to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = args.out_dir
    if out_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("Recordings") / f"biofeedback_rec_{stamp}"

    stop_event = threading.Event()

    def _on_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    if args.stdin_stop:
        def _watch_stdin() -> None:
            try:
                for line in sys.stdin:
                    if line.strip().upper() == "STOP":
                        stop_event.set()
                        return
            except Exception:
                pass
            stop_event.set()

        t_stdin = threading.Thread(target=_watch_stdin, name="RecorderStdinWatcher", daemon=True)
        t_stdin.start()

    recorder = LSLSessionRecorder(
        out_dir=out_dir,
        raw_stream=args.raw_stream,
        filtered_stream=args.filtered_stream,
        features_stream=args.features_stream,
        events_stream=args.events_stream,
        resolve_timeout_s=args.resolve_timeout,
    )

    def _progress(stats: dict[str, object]) -> None:
        if args.json_progress:
            print(f"REC_STATUS {json.dumps(stats)}", flush=True)
        else:
            print(
                f"Recording | raw={stats['raw']} filt={stats['filtered']} "
                f"feat={stats['features']} ev={stats['events']} -> {stats['out_dir']}",
                flush=True,
            )

    try:
        meta = recorder.run(stop_event=stop_event, progress_cb=_progress)
    except Exception as exc:
        print(f"Recorder error: {exc}", file=sys.stderr, flush=True)
        return 1

    if not args.json_progress:
        print(f"Saved recording metadata to {out_dir / 'recording_meta.json'}", flush=True)
    else:
        print(f"REC_DONE {json.dumps(meta)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
