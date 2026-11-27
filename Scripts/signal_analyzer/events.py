"""Extensible event detector registry, QRS detector, and event statistics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from scipy import signal as sp_signal

from .models import ChannelState, EventDetectionResult


@dataclass(frozen=True)
class DetectorSpec:
    detector_type: str
    display_name: str
    marker_symbol: str
    marker_color: str
    default_params: dict[str, Any]
    detect_fn: Callable[..., EventDetectionResult]


def detect_qrs(
    time_s: np.ndarray,
    fs: float,
    raw_signal: np.ndarray,
    processed_signal: np.ndarray,
    channel_index: int,
    channel_name: str,
    params: dict[str, Any] | None = None,
) -> EventDetectionResult:
    """Detect QRS R-peaks on the processed signal using a Pan-Tompkins-style pipeline.

    Returns exact sample indices, timestamps, processed-signal amplitudes, and
    raw-signal amplitudes at those indices.
    """
    p = dict(DETECTOR_REGISTRY["qrs"].default_params)
    if params:
        p.update(params)

    proc = np.asarray(processed_signal, dtype=float)
    raw = np.asarray(raw_signal, dtype=float)
    t = np.asarray(time_s, dtype=float)

    empty = EventDetectionResult(
        detector_type="qrs",
        event_label="QRS",
        channel_index=channel_index,
        channel_name=channel_name,
        indices=np.array([], dtype=int),
        times=np.array([], dtype=float),
        processed_values=np.array([], dtype=float),
        raw_values=np.array([], dtype=float),
        marker_symbol="t",
        marker_color="#D62728",
    )

    if len(proc) < 10 or fs <= 0:
        return empty

    finite_mask = np.isfinite(proc)
    if not np.any(finite_mask):
        return empty
    proc_clean = proc.copy()
    if not np.all(finite_mask):
        idx_all = np.arange(len(proc_clean))
        proc_clean[~finite_mask] = np.interp(
            idx_all[~finite_mask], idx_all[finite_mask], proc_clean[finite_mask]
        )

    if float(np.ptp(proc_clean)) < 1e-12:
        return empty

    work = proc_clean - float(np.median(proc_clean))

    # Optional internal Pan-Tompkins bandpass (5-15 Hz clamped below Nyquist)
    use_bp = bool(p.get("use_internal_bandpass", True))
    if use_bp:
        nyq = fs * 0.5
        lo = max(0.5, float(p.get("bp_low_hz", 5.0)))
        hi = min(float(p.get("bp_high_hz", 15.0)), nyq * 0.92)
        if lo < hi and hi < nyq and len(work) > 18:
            sos = sp_signal.butter(2, [lo / nyq, hi / nyq], btype="bandpass", output="sos")
            work = sp_signal.sosfiltfilt(sos, work)

    # Derivative -> Squaring -> Moving Window Integration
    deriv = np.gradient(work)
    squared = deriv * deriv
    mwi_samples = max(1, int(round(float(p.get("mwi_ms", 150.0)) * 1e-3 * fs)))
    kernel = np.ones(mwi_samples, dtype=float) / mwi_samples
    mwi = np.convolve(squared, kernel, mode="same")

    refractory_samples = max(1, int(round(float(p.get("refractory_ms", 250.0)) * 1e-3 * fs)))
    threshold_factor = max(0.01, float(p.get("threshold_factor", 0.35)))
    ref_level = float(np.percentile(mwi, 95))
    min_height = threshold_factor * ref_level
    if min_height <= 0:
        return empty

    cand_peaks, _ = sp_signal.find_peaks(
        mwi,
        height=min_height,
        distance=refractory_samples,
        prominence=min_height * 0.4,
    )
    if len(cand_peaks) == 0:
        return empty

    # Determine dominant R-wave polarity on the processed signal
    polarity = str(p.get("polarity", "auto")).lower()
    baseline = float(np.median(proc_clean))
    centered = proc_clean - baseline
    if polarity == "auto":
        pos_amp = float(np.percentile(centered, 99.5))
        neg_amp = float(np.abs(np.percentile(centered, 0.5)))
        polarity = "positive" if pos_amp >= neg_amp else "negative"

    search_half = max(1, int(round(float(p.get("search_window_ms", 80.0)) * 1e-3 * fs)))
    refined: list[int] = []
    for c_idx in cand_peaks:
        start = max(0, int(c_idx) - search_half)
        stop = min(len(proc_clean), int(c_idx) + search_half + 1)
        seg = proc_clean[start:stop]
        if len(seg) == 0:
            continue
        local_idx = int(np.argmax(seg) if polarity == "positive" else np.argmin(seg))
        refined.append(start + local_idx)

    if not refined:
        return empty

    # Deduplicate any two refined peaks closer than refractory_samples
    deduped: list[int] = [refined[0]]
    for idx in refined[1:]:
        if idx - deduped[-1] < refractory_samples:
            prev = deduped[-1]
            better = (
                (proc_clean[idx] > proc_clean[prev])
                if polarity == "positive"
                else (proc_clean[idx] < proc_clean[prev])
            )
            if better:
                deduped[-1] = idx
        else:
            deduped.append(idx)

    idx_arr = np.asarray(deduped, dtype=int)
    return EventDetectionResult(
        detector_type="qrs",
        event_label="QRS",
        channel_index=channel_index,
        channel_name=channel_name,
        indices=idx_arr,
        times=t[idx_arr],
        processed_values=proc[idx_arr],
        raw_values=raw[idx_arr],
        marker_symbol="t",
        marker_color="#D62728",
    )


DETECTOR_REGISTRY: dict[str, DetectorSpec] = {
    "qrs": DetectorSpec(
        detector_type="qrs",
        display_name="QRS Detection",
        marker_symbol="t",
        marker_color="#D62728",
        default_params={
            "use_internal_bandpass": True,
            "bp_low_hz": 5.0,
            "bp_high_hz": 15.0,
            "mwi_ms": 150.0,
            "refractory_ms": 250.0,
            "threshold_factor": 0.35,
            "search_window_ms": 80.0,
            "polarity": "auto",
        },
        detect_fn=detect_qrs,
    ),
}


def run_channel_detectors(
    channel: ChannelState,
    time_s: np.ndarray,
    fs: float,
    raw_signal: np.ndarray,
    processed_signal: np.ndarray,
) -> list[EventDetectionResult]:
    """Run all enabled detectors configured on a ChannelState."""
    results: list[EventDetectionResult] = []
    for det_key, cfg in channel.detectors.items():
        if not cfg.enabled:
            continue
        spec = DETECTOR_REGISTRY.get(det_key)
        if spec is None:
            continue
        res = spec.detect_fn(
            time_s=time_s,
            fs=fs,
            raw_signal=raw_signal,
            processed_signal=processed_signal,
            channel_index=channel.index,
            channel_name=channel.display_name,
            params=cfg.params,
        )
        results.append(res)
    return results


def compute_event_stats(res: EventDetectionResult) -> dict[str, Any]:
    """Compute Count, interval mean/SD, processed amp mean/SD, raw amp mean/SD."""
    count = int(len(res.indices))
    if count >= 2:
        intervals = np.diff(res.times)
        mean_int = float(np.mean(intervals))
        sd_int = float(np.std(intervals, ddof=1)) if len(intervals) >= 2 else 0.0
    else:
        mean_int = None
        sd_int = None

    if count >= 1:
        proc_vals = res.processed_values[np.isfinite(res.processed_values)]
        raw_vals = res.raw_values[np.isfinite(res.raw_values)]
        mean_amp = float(np.mean(proc_vals)) if len(proc_vals) >= 1 else None
        sd_amp = float(np.std(proc_vals, ddof=1)) if len(proc_vals) >= 2 else (0.0 if len(proc_vals) == 1 else None)
        mean_raw = float(np.mean(raw_vals)) if len(raw_vals) >= 1 else None
        sd_raw = float(np.std(raw_vals, ddof=1)) if len(raw_vals) >= 2 else (0.0 if len(raw_vals) == 1 else None)
    else:
        mean_amp = sd_amp = mean_raw = sd_raw = None

    return {
        "event_label": f"{res.event_label} ({res.channel_name})",
        "count": count,
        "mean_interval_s": mean_int,
        "sd_interval_s": sd_int,
        "frequency_hz": (1.0 / mean_int) if (mean_int is not None and mean_int > 0) else None,
        "bpm": (60.0 / mean_int) if (mean_int is not None and mean_int > 0) else None,
        "mean_amp": mean_amp,
        "sd_amp": sd_amp,
        "mean_raw_amp": mean_raw,
        "sd_raw_amp": sd_raw,
    }


ROLLING_STAT_LABELS: dict[str, str] = {
    "count": "Count",
    "mean_interval_s": "Mean Interval (s)",
    "sd_interval_s": "SD Interval (s)",
    "frequency_hz": "Frequency (Hz)",
    "bpm": "BPM",
    "mean_amp": "Amplitude",
    "mean_raw_amp": "Raw Amplitude",
}


def compute_rolling_event_stat(
    time_s: np.ndarray,
    res: EventDetectionResult,
    stat_key: str,
    window_s: float = 5.0,
    step_s: float = 0.5,
) -> np.ndarray:
    """Compute a rolling statistic over time in `window_s` windows stepped by `step_s`.

    Evaluated at bin centers spaced by `step_s` and linearly interpolated onto `time_s`
    so the resulting trace behaves as a full-rate derived channel.
    """
    t = np.asarray(time_s, dtype=float)
    if len(t) == 0:
        return np.array([], dtype=float)

    t_start = float(t[0])
    t_end = float(t[-1])
    win = max(1e-3, float(window_s))
    step = max(1e-3, float(step_s))

    if t_end - t_start < step:
        centers = np.array([0.5 * (t_start + t_end)], dtype=float)
    else:
        centers = np.arange(t_start, t_end + 1e-9, step, dtype=float)

    ev_t = np.asarray(res.times, dtype=float)
    if len(ev_t) == 0:
        return np.zeros_like(t, dtype=float)

    proc_v = np.asarray(res.processed_values, dtype=float)
    raw_v = np.asarray(res.raw_values, dtype=float)
    rr = np.diff(ev_t) if len(ev_t) >= 2 else np.array([], dtype=float)
    rr_t = ev_t[1:] if len(ev_t) >= 2 else np.array([], dtype=float)

    half = 0.5 * win
    bin_vals = np.full(len(centers), np.nan, dtype=float)

    for i, tc in enumerate(centers):
        lo = tc - half
        hi = tc + half
        ev_mask = (ev_t >= lo) & ((ev_t <= hi) if hi >= t_end else (ev_t < hi))

        if stat_key == "count":
            bin_vals[i] = float(np.sum(ev_mask))
        elif stat_key in {"mean_interval_s", "sd_interval_s", "frequency_hz", "bpm"}:
            if len(rr_t) == 0:
                continue
            rr_mask = (rr_t >= lo) & ((rr_t <= hi) if hi >= t_end else (rr_t < hi))
            rr_win = rr[rr_mask]
            if stat_key == "mean_interval_s" and len(rr_win) >= 1:
                bin_vals[i] = float(np.mean(rr_win))
            elif stat_key == "sd_interval_s" and len(rr_win) >= 2:
                bin_vals[i] = float(np.std(rr_win, ddof=1))
            elif stat_key in {"frequency_hz", "bpm"} and len(rr_win) >= 1:
                m_rr = float(np.mean(rr_win))
                if m_rr > 0:
                    bin_vals[i] = (60.0 / m_rr) if stat_key == "bpm" else (1.0 / m_rr)
        elif stat_key == "mean_amp":
            vals = proc_v[ev_mask]
            vals = vals[np.isfinite(vals)]
            if len(vals) >= 1:
                bin_vals[i] = float(np.mean(vals))
        elif stat_key == "mean_raw_amp":
            vals = raw_v[ev_mask]
            vals = vals[np.isfinite(vals)]
            if len(vals) >= 1:
                bin_vals[i] = float(np.mean(vals))

    finite = np.isfinite(bin_vals)
    if not np.any(finite):
        return np.zeros_like(t, dtype=float)
    if np.sum(finite) == 1:
        return np.full_like(t, float(bin_vals[finite][0]), dtype=float)
    return np.interp(t, centers[finite], bin_vals[finite])
