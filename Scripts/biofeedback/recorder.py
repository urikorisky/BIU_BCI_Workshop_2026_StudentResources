"""Separate-process LSL session recorder for raw inputs, filtered signals, features, and events."""

from __future__ import annotations

import csv
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .lsl_io import LSLInputSource

_STUDIO_OUTPUT_NAMES = {"BiofeedbackFeatures", "BiofeedbackEvents", "BiofeedbackFiltered"}


@dataclass
class _RecordedStream:
    role: str
    filename: str
    info: Any
    inlet: Any
    channel_labels: list[str]
    is_events: bool
    file_handle: Any
    writer: Any
    sample_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class LSLSessionRecorder:
    """Resolve and record incoming raw, filtered, feature, and event LSL streams to CSV."""

    def __init__(
        self,
        out_dir: Path,
        raw_stream: str | None = None,
        filtered_stream: str = "BiofeedbackFiltered",
        features_stream: str = "BiofeedbackFeatures",
        events_stream: str = "BiofeedbackEvents",
        resolve_timeout_s: float = 6.0,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.raw_stream = raw_stream
        self.filtered_stream = filtered_stream
        self.features_stream = features_stream
        self.events_stream = events_stream
        self.resolve_timeout_s = max(0.5, float(resolve_timeout_s))

        self._t0_lsl: float | None = None
        self._streams: dict[str, _RecordedStream] = {}
        self._started_utc: str = ""
        self._ended_utc: str = ""

    @staticmethod
    def _match_stream(streams: list[Any], target_name: str) -> Any | None:
        target = target_name.strip().lower()
        if not target:
            return None
        for info in streams:
            if str(info.name()).strip().lower() == target:
                return info
        for info in streams:
            if target in str(info.name()).strip().lower():
                return info
        return None

    def _pick_raw_stream(self, streams: list[Any]) -> Any | None:
        if self.raw_stream and self.raw_stream.strip().lower() != "auto":
            return self._match_stream(streams, self.raw_stream)

        candidates: list[Any] = []
        for info in streams:
            name = str(info.name())
            stype = str(info.type()).lower()
            if name in _STUDIO_OUTPUT_NAMES:
                continue
            if stype in {"markers", "marker", "events", "event"}:
                continue
            candidates.append(info)

        if not candidates:
            return None
        for info in candidates:
            if str(info.name()) != "SyntheticBioSignals":
                return info
        return candidates[0]

    def _resolve_targets(self) -> dict[str, Any]:
        from pylsl import resolve_streams

        resolved: dict[str, Any] = {}
        deadline = time.monotonic() + self.resolve_timeout_s
        while time.monotonic() <= deadline:
            streams = resolve_streams(wait_time=0.5)
            if "raw" not in resolved:
                raw_info = self._pick_raw_stream(streams)
                if raw_info is not None:
                    resolved["raw"] = raw_info
            if "filtered" not in resolved:
                flt_info = self._match_stream(streams, self.filtered_stream)
                if flt_info is not None:
                    resolved["filtered"] = flt_info
            if "features" not in resolved:
                feat_info = self._match_stream(streams, self.features_stream)
                if feat_info is not None:
                    resolved["features"] = feat_info
            if "events" not in resolved:
                ev_info = self._match_stream(streams, self.events_stream)
                if ev_info is not None:
                    resolved["events"] = ev_info

            if len(resolved) == 4:
                break
            time.sleep(0.1)
        return resolved

    def _open_streams(self, resolved: dict[str, Any]) -> None:
        from pylsl import StreamInlet

        self.out_dir.mkdir(parents=True, exist_ok=True)
        role_files = {
            "raw": ("incoming_raw.csv", False),
            "filtered": ("filtered_output.csv", False),
            "features": ("features.csv", False),
            "events": ("events.csv", True),
        }

        for role, (fname, is_events) in role_files.items():
            info = resolved.get(role)
            if info is None:
                continue
            inlet = StreamInlet(info, max_buflen=30, recover=True)
            full_info = inlet.info()
            n_ch = int(full_info.channel_count())
            labels = LSLInputSource._extract_labels(full_info, n_ch)

            fh = (self.out_dir / fname).open("w", encoding="utf-8", newline="")
            writer = csv.writer(fh)
            if is_events:
                writer.writerow(
                    ["time_s", "lsl_timestamp", "kind", "event_time_s", "amplitude", "confidence", "raw_json"]
                )
            else:
                writer.writerow(["time_s", "lsl_timestamp", *labels])

            self._streams[role] = _RecordedStream(
                role=role,
                filename=fname,
                info=full_info,
                inlet=inlet,
                channel_labels=labels,
                is_events=is_events,
                file_handle=fh,
                writer=writer,
                meta={
                    "name": str(full_info.name()),
                    "type": str(full_info.type()),
                    "nominal_srate": float(full_info.nominal_srate()),
                    "channel_count": n_ch,
                    "channel_labels": labels,
                    "file": fname,
                },
            )

    def _drain_stream(self, rec: _RecordedStream) -> int:
        total = 0
        for _ in range(8):
            samples, timestamps = rec.inlet.pull_chunk(timeout=0.0, max_samples=2048)
            if not timestamps:
                break
            if self._t0_lsl is None:
                self._t0_lsl = float(timestamps[0])
            t0 = self._t0_lsl

            if rec.is_events:
                for row, ts in zip(samples, timestamps):
                    ts_f = float(ts)
                    raw_str = str(row[0]) if row else "{}"
                    kind = ""
                    ev_t = ""
                    amp = ""
                    conf = ""
                    try:
                        parsed = json.loads(raw_str)
                        kind = str(parsed.get("kind", ""))
                        if "time_s" in parsed:
                            ev_t = f"{float(parsed['time_s']):.4f}"
                        if "amplitude" in parsed:
                            amp = f"{float(parsed['amplitude']):.6g}"
                        if "confidence" in parsed:
                            conf = f"{float(parsed['confidence']):.4f}"
                    except Exception:
                        pass
                    rec.writer.writerow(
                        [f"{ts_f - t0:.6f}", f"{ts_f:.6f}", kind, ev_t, amp, conf, raw_str]
                    )
            else:
                for row, ts in zip(samples, timestamps):
                    ts_f = float(ts)
                    rec.writer.writerow(
                        [f"{ts_f - t0:.6f}", f"{ts_f:.6f}", *[f"{float(v):.6g}" for v in row]]
                    )

            rec.sample_count += len(timestamps)
            total += len(timestamps)
            if len(timestamps) < 2048:
                break
        return total

    def get_stats(self) -> dict[str, Any]:
        return {
            "raw": self._streams["raw"].sample_count if "raw" in self._streams else 0,
            "filtered": self._streams["filtered"].sample_count if "filtered" in self._streams else 0,
            "features": self._streams["features"].sample_count if "features" in self._streams else 0,
            "events": self._streams["events"].sample_count if "events" in self._streams else 0,
            "streams": sorted(self._streams.keys()),
            "out_dir": str(self.out_dir),
        }

    def run(
        self,
        stop_event: threading.Event,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Resolve streams, record until stop_event is set, flush, and write metadata JSON."""
        self._started_utc = datetime.now(timezone.utc).isoformat()
        resolved = self._resolve_targets()
        if not resolved:
            raise RuntimeError(
                "No target LSL streams could be resolved within timeout "
                f"({self.resolve_timeout_s:.1f}s)."
            )

        self._open_streams(resolved)
        if progress_cb is not None:
            progress_cb(self.get_stats())

        last_flush = time.monotonic()
        try:
            while not stop_event.is_set():
                for rec in self._streams.values():
                    self._drain_stream(rec)

                now = time.monotonic()
                if now - last_flush >= 1.0:
                    for rec in self._streams.values():
                        rec.file_handle.flush()
                    if progress_cb is not None:
                        progress_cb(self.get_stats())
                    last_flush = now

                stop_event.wait(0.015)

            # Final drain after stop requested
            time.sleep(0.05)
            for rec in self._streams.values():
                self._drain_stream(rec)
                rec.file_handle.flush()
        finally:
            self._ended_utc = datetime.now(timezone.utc).isoformat()
            for rec in self._streams.values():
                try:
                    rec.file_handle.flush()
                    rec.file_handle.close()
                except Exception:
                    pass
                try:
                    rec.inlet.close_stream()
                except Exception:
                    pass

            meta_payload = {
                "version": 1,
                "started_utc": self._started_utc,
                "ended_utc": self._ended_utc,
                "t0_lsl": self._t0_lsl,
                "out_dir": str(self.out_dir),
                "streams": {
                    role: {**rec.meta, "sample_count": int(rec.sample_count)}
                    for role, rec in self._streams.items()
                },
            }
            (self.out_dir / "recording_meta.json").write_text(
                json.dumps(meta_payload, indent=2), encoding="utf-8"
            )
            if progress_cb is not None:
                progress_cb(self.get_stats())

        return meta_payload
