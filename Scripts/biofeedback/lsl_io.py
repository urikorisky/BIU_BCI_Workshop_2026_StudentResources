"""Encapsulated LSL input/output and built-in synthetic ECG+EDA simulator."""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np

from .models import DetectedEvent, FEATURE_CHANNELS, FILTERED_CHANNELS, FeatureSnapshot

_OWN_STREAM_NAMES = {
    "BiofeedbackFeatures",
    "BiofeedbackEvents",
    "BiofeedbackFiltered",
    "SyntheticBioSignals",
}


def discover_lsl_streams(timeout_s: float = 1.0) -> list[dict[str, Any]]:
    """Resolve active LSL numeric streams while filtering out our own broadcast outlets."""
    from pylsl import resolve_streams

    streams = resolve_streams(wait_time=timeout_s)
    found: list[dict[str, Any]] = []
    for info in streams:
        name = info.name()
        stype = info.type()
        if name in _OWN_STREAM_NAMES:
            continue
        if stype.lower() in {"markers", "marker", "events", "event"}:
            continue
        found.append(
            {
                "name": name,
                "type": stype,
                "channel_count": int(info.channel_count()),
                "nominal_srate": float(info.nominal_srate()),
                "source_id": info.source_id(),
                "uid": info.uid(),
                "info": info,
            }
        )
    return found


class LSLInputSource:
    """Non-blocking LSL StreamInlet wrapper with effective sample rate tracking."""

    def __init__(self, stream_info: Any) -> None:
        from pylsl import StreamInlet

        self.inlet = StreamInlet(stream_info, max_buflen=10, recover=True)
        self.name = str(stream_info.name())
        self.nominal_srate = float(stream_info.nominal_srate())
        self.channel_count = int(stream_info.channel_count())
        self.channel_labels = self._extract_labels(self.inlet.info(), self.channel_count)
        self._t0: float | None = None
        self._total_samples = 0
        self._first_ts: float | None = None
        self._last_ts: float | None = None

    @staticmethod
    def _extract_labels(info: Any, n_ch: int) -> list[str]:
        labels: list[str] = []
        try:
            ch = info.desc().child("channels").child("channel")
            while not ch.empty():
                lbl = ch.child_value("label")
                labels.append(lbl if lbl else f"Ch{len(labels) + 1}")
                ch = ch.next_sibling("channel")
        except Exception:
            pass
        if len(labels) == n_ch:
            return labels
        return [f"Ch{i + 1}" for i in range(n_ch)]

    @property
    def effective_srate(self) -> float:
        if self.nominal_srate > 0:
            return self.nominal_srate
        if (
            self._total_samples >= 20
            and self._first_ts is not None
            and self._last_ts is not None
            and (self._last_ts - self._first_ts) > 0.2
        ):
            return float((self._total_samples - 1) / (self._last_ts - self._first_ts))
        return 250.0

    def pull(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Drain queued LSL chunks and return (t_rel, lsl_timestamps, data_matrix)."""
        chunk_samples: list[np.ndarray] = []
        chunk_ts: list[np.ndarray] = []
        for _ in range(4):
            samples, timestamps = self.inlet.pull_chunk(timeout=0.0, max_samples=2048)
            if not timestamps:
                break
            arr = np.asarray(samples, dtype=float)
            if arr.ndim == 1:
                arr = arr[:, np.newaxis]
            chunk_samples.append(arr)
            chunk_ts.append(np.asarray(timestamps, dtype=float))
            if len(timestamps) < 2048:
                break

        if not chunk_ts:
            return (
                np.empty(0, dtype=float),
                np.empty(0, dtype=float),
                np.empty((0, self.channel_count), dtype=float),
            )

        lsl_t = chunk_ts[0] if len(chunk_ts) == 1 else np.concatenate(chunk_ts)
        data = chunk_samples[0] if len(chunk_samples) == 1 else np.vstack(chunk_samples)
        if self._t0 is None:
            self._t0 = float(lsl_t[0])
            self._first_ts = float(lsl_t[0])
        self._last_ts = float(lsl_t[-1])
        self._total_samples += len(lsl_t)

        t_rel = lsl_t - self._t0
        return t_rel, lsl_t, data

    def close(self) -> None:
        try:
            self.inlet.close_stream()
        except Exception:
            pass


class SyntheticBioSource:
    """Realistic noisy 2-channel [ECG, EDA] simulator for offline testing & classroom demos."""

    def __init__(self, fs: float = 250.0, seed: int = 42) -> None:
        self.fs = float(fs)
        self.nominal_srate = self.fs
        self.channel_count = 2
        self.channel_labels = ["ECG (Sim)", "EDA (Sim)"]
        self._rng = np.random.default_rng(seed)
        self._last_wall = time.monotonic()
        self._sample_idx = 0
        self._t0_lsl: float | None = None
        self._scr_period_s = 7.0
        self._last_scr_gen = -5.0
        self._scr_onsets: list[float] = []

    def reset(self) -> None:
        self._last_wall = time.monotonic()
        self._sample_idx = 0
        self._t0_lsl = None
        self._last_scr_gen = -5.0
        self._scr_onsets.clear()

    def _ecg_waveform(self, t: np.ndarray) -> np.ndarray:
        # ~72 bpm (1.2 Hz) with mild respiratory sinus arrhythmia modulation
        phase = (1.2 * t + 0.04 * np.sin(2.0 * np.pi * 0.22 * t)) % 1.0
        p = 0.12 * np.exp(-(((phase - 0.18) / 0.032) ** 2))
        q = -0.18 * np.exp(-(((phase - 0.285) / 0.012) ** 2))
        r = 1.05 * np.exp(-(((phase - 0.31) / 0.014) ** 2))
        s = -0.25 * np.exp(-(((phase - 0.335) / 0.014) ** 2))
        t_wave = 0.28 * np.exp(-(((phase - 0.56) / 0.055) ** 2))
        hum = 0.18 * np.sin(2.0 * np.pi * 50.0 * t)
        noise = self._rng.normal(0.0, 0.035, size=len(t))
        return 1.65 + p + q + r + s + t_wave + hum + noise

    def _eda_waveform(self, t: np.ndarray) -> np.ndarray:
        tonic = 12.0 + 0.6 * np.sin(2.0 * np.pi * 0.015 * t)
        phasic = np.zeros_like(t)
        if len(t) > 0:
            while self._last_scr_gen + self._scr_period_s <= float(t[-1]):
                self._last_scr_gen += self._scr_period_s
                self._scr_onsets.append(self._last_scr_gen)
            # Keep only recent onsets contributing to current chunk
            self._scr_onsets = [o for o in self._scr_onsets if float(t[0]) - o < 16.0]
            for onset in self._scr_onsets:
                dt = t - onset
                active = dt > 0
                if np.any(active):
                    d = dt[active]
                    phasic[active] += 1.6 * (np.exp(-d / 2.5) - np.exp(-d / 0.75))
        noise = self._rng.normal(0.0, 0.03, size=len(t))
        return tonic + phasic + noise

    def generate_n(self, n_samples: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Generate an exact number of samples (deterministic for tests)."""
        from pylsl import local_clock

        if self._t0_lsl is None:
            self._t0_lsl = float(local_clock())
        idx = np.arange(self._sample_idx, self._sample_idx + n_samples, dtype=float)
        self._sample_idx += n_samples
        t_rel = idx / self.fs
        lsl_t = self._t0_lsl + t_rel
        ecg = self._ecg_waveform(t_rel)
        eda = self._eda_waveform(t_rel)
        return t_rel, lsl_t, np.column_stack([ecg, eda])

    def pull(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - self._last_wall))
        n = int(dt * self.fs)
        if n <= 0:
            return np.empty(0, dtype=float), np.empty(0, dtype=float), np.empty((0, 2), dtype=float)
        self._last_wall += n / self.fs
        return self.generate_n(n)

    def close(self) -> None:
        pass


class BiofeedbackLSLOutlets:
    """Encapsulated LSL output streams for downstream stimulus/recording scripts."""

    def __init__(
        self,
        feature_rate_hz: float = 25.0,
        sample_rate_hz: float = 250.0,
        raw_channel_labels: list[str] | None = None,
    ) -> None:
        from pylsl import StreamInfo, StreamOutlet, cf_float32, cf_string

        f_info = StreamInfo(
            name="BiofeedbackFeatures",
            type="Biofeedback",
            channel_count=len(FEATURE_CHANNELS),
            nominal_srate=float(feature_rate_hz),
            channel_format=cf_float32,
            source_id="biu_bci_biofeedback_features_2026",
        )
        chns = f_info.desc().append_child("channels")
        for lbl, unit in FEATURE_CHANNELS:
            ch = chns.append_child("channel")
            ch.append_child_value("label", lbl)
            ch.append_child_value("unit", unit)
            ch.append_child_value("type", "Biofeedback")

        e_info = StreamInfo(
            name="BiofeedbackEvents",
            type="Markers",
            channel_count=1,
            nominal_srate=0.0,
            channel_format=cf_string,
            source_id="biu_bci_biofeedback_events_2026",
        )

        flt_info = StreamInfo(
            name="BiofeedbackFiltered",
            type="BiofeedbackFiltered",
            channel_count=len(FILTERED_CHANNELS),
            nominal_srate=float(sample_rate_hz),
            channel_format=cf_float32,
            source_id="biu_bci_biofeedback_filtered_2026",
        )
        flt_chns = flt_info.desc().append_child("channels")
        for lbl, unit in FILTERED_CHANNELS:
            ch = flt_chns.append_child("channel")
            ch.append_child_value("label", lbl)
            ch.append_child_value("unit", unit)
            ch.append_child_value("type", "BiofeedbackFiltered")

        self.feature_outlet: StreamOutlet | None = StreamOutlet(f_info)
        self.event_outlet: StreamOutlet | None = StreamOutlet(e_info)
        self.filtered_outlet: StreamOutlet | None = StreamOutlet(flt_info)
        self.raw_outlet: StreamOutlet | None = None

        if raw_channel_labels:
            r_info = StreamInfo(
                name="SyntheticBioSignals",
                type="BioSignals",
                channel_count=len(raw_channel_labels),
                nominal_srate=float(sample_rate_hz),
                channel_format=cf_float32,
                source_id="biu_bci_synthetic_raw_2026",
            )
            r_chns = r_info.desc().append_child("channels")
            for lbl in raw_channel_labels:
                ch = r_chns.append_child("channel")
                ch.append_child_value("label", str(lbl))
                ch.append_child_value("type", "BioSignals")
            self.raw_outlet = StreamOutlet(r_info)

        self._feature_period_s = 1.0 / max(1.0, float(feature_rate_hz))
        self._last_feature_push_wall: float = 0.0

    def push_features(
        self, snapshot: FeatureSnapshot, lsl_ts: float, force: bool = False
    ) -> bool:
        """Push feature vector, rate-limited to feature_rate_hz unless force=True."""
        if self.feature_outlet is None:
            return False
        now = time.monotonic()
        if not force and (now - self._last_feature_push_wall) < self._feature_period_s:
            return False
        self.feature_outlet.push_sample(snapshot.to_list(), timestamp=float(lsl_ts))
        self._last_feature_push_wall = now
        return True

    def push_event(self, ev: DetectedEvent) -> None:
        if self.event_outlet is not None:
            payload = json.dumps(
                {
                    "kind": ev.kind,
                    "time_s": round(ev.time_s, 4),
                    "amplitude": round(ev.amplitude, 4),
                    "confidence": round(ev.confidence, 4),
                }
            )
            self.event_outlet.push_sample([payload], timestamp=float(ev.lsl_time))

    def push_filtered_chunk(self, rows: np.ndarray, lsl_timestamps: np.ndarray) -> None:
        """Publish full-rate causal filtered chunk to BiofeedbackFiltered outlet."""
        if self.filtered_outlet is None or len(rows) == 0:
            return
        arr = np.ascontiguousarray(rows, dtype=np.float32)
        self.filtered_outlet.push_chunk(arr, timestamp=np.asarray(lsl_timestamps, dtype=float).tolist())

    def push_raw_chunk(self, rows: np.ndarray, lsl_timestamps: np.ndarray) -> None:
        """Publish synthetic raw input chunk to SyntheticBioSignals outlet for recorder loopback."""
        if self.raw_outlet is None or len(rows) == 0:
            return
        arr = np.ascontiguousarray(rows, dtype=np.float32)
        self.raw_outlet.push_chunk(arr, timestamp=np.asarray(lsl_timestamps, dtype=float).tolist())

    def close(self) -> None:
        self.feature_outlet = None
        self.event_outlet = None
        self.filtered_outlet = None
        self.raw_outlet = None
