"""Causal real-time filtering, multi-resolution display ring buffers, and rolling stats."""

from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np
from scipy import signal as sp_signal

from .models import CausalFilterSpec, PipelineConfig


def default_realtime_filter_params(filter_type: str, fs: float) -> dict[str, Any]:
    """Return Nyquist-safe default parameters for a causal real-time filter stage."""
    nyq = max(float(fs) * 0.5, 0.5)
    lp_def = round(min(40.0, nyq * 0.45), 2)
    hp_def = round(min(0.5, nyq * 0.1), 2)
    bp_lo = round(min(5.0, nyq * 0.15), 2)
    bp_hi = round(min(35.0, nyq * 0.45), 2)
    if bp_lo >= bp_hi:
        bp_lo = round(nyq * 0.1, 2)
        bp_hi = round(nyq * 0.45, 2)
    if filter_type == "lowpass":
        return {"cutoff_hz": lp_def, "order": 4}
    if filter_type == "highpass":
        return {"cutoff_hz": hp_def, "order": 4}
    if filter_type == "bandpass":
        return {"low_hz": bp_lo, "high_hz": bp_hi, "order": 4}
    if filter_type == "bandstop":
        bs_lo = round(min(48.0, nyq * 0.35), 2)
        bs_hi = round(min(52.0, nyq * 0.45), 2)
        if bs_lo >= bs_hi:
            bs_lo = round(nyq * 0.30, 2)
            bs_hi = round(nyq * 0.45, 2)
        return {"low_hz": bs_lo, "high_hz": bs_hi, "order": 4}
    return {"order": 4}


def build_causal_filter_chain(
    specs: list[CausalFilterSpec], fs: float
) -> tuple[list["CausalSOSFilter"], dict[str, str]]:
    """Build validated stateful CausalSOSFilter chain from ordered specs."""
    nyq = 0.5 * max(float(fs), 1.0)
    chain: list[CausalSOSFilter] = []
    errors: dict[str, str] = {}
    for spec in specs:
        if not spec.enabled:
            continue
        ftype = spec.filter_type
        order = max(1, min(8, int(spec.params.get("order", 4))))
        if ftype in {"lowpass", "highpass"}:
            fc = float(spec.params.get("cutoff_hz", 10.0))
            if fc <= 0.0:
                errors[spec.id] = "Cutoff frequency must be > 0 Hz."
                continue
            if fc >= nyq:
                errors[spec.id] = f"Cutoff ({fc:.2f} Hz) must be < Nyquist ({nyq:.2f} Hz)."
                continue
            filt = CausalSOSFilter(fs, ftype, fc, order=order)
            if filt.sos is None:
                errors[spec.id] = f"Cutoff ({fc:.2f} Hz) exceeds safe filter limit ({0.48 * fs:.2f} Hz)."
                continue
            chain.append(filt)
        elif ftype in {"bandpass", "bandstop"}:
            lo = float(spec.params.get("low_hz", 1.0))
            hi = float(spec.params.get("high_hz", 35.0))
            if lo <= 0.0 or hi <= 0.0:
                errors[spec.id] = "Frequencies must be > 0 Hz."
                continue
            if lo >= hi:
                errors[spec.id] = "Low cutoff must be less than high cutoff."
                continue
            if hi >= nyq:
                errors[spec.id] = f"High cutoff ({hi:.2f} Hz) must be < Nyquist ({nyq:.2f} Hz)."
                continue
            filt = CausalSOSFilter(fs, ftype, (lo, hi), order=order)
            if filt.sos is None:
                errors[spec.id] = f"High cutoff ({hi:.2f} Hz) exceeds safe filter limit ({0.48 * fs:.2f} Hz)."
                continue
            chain.append(filt)
        else:
            errors[spec.id] = f"Unsupported filter type: {ftype}"
    return chain, errors


class CausalSOSFilter:
    """Stateful causal Butterworth IIR filter preserving continuity across chunks."""

    def __init__(
        self,
        fs: float,
        btype: str,
        cutoff: float | tuple[float, float],
        order: int = 4,
    ) -> None:
        self.fs = max(float(fs), 1.0)
        self.btype = btype
        self.order = max(1, int(order))
        self.sos: np.ndarray | None = None
        self.zi: np.ndarray | None = None
        self._seeded = False

        nyq = 0.5 * self.fs
        max_f = 0.48 * self.fs
        if isinstance(cutoff, tuple):
            lo = max(1e-4, min(float(cutoff[0]), max_f * 0.95))
            hi = max(lo + 1e-4, min(float(cutoff[1]), max_f))
            if lo < hi < nyq:
                self.sos = sp_signal.butter(self.order, [lo / nyq, hi / nyq], btype=btype, output="sos")
        else:
            fc = float(cutoff)
            if 0.0 < fc < max_f and fc < nyq:
                self.sos = sp_signal.butter(self.order, fc / nyq, btype=btype, output="sos")

        if self.sos is not None:
            self._zi_template = sp_signal.sosfilt_zi(self.sos)
        else:
            self._zi_template = None

    def reset(self) -> None:
        self.zi = None
        self._seeded = False

    def process(self, chunk: np.ndarray) -> np.ndarray:
        x = np.asarray(chunk, dtype=float)
        if len(x) == 0 or self.sos is None:
            return x.copy()
        if not np.all(np.isfinite(x)):
            x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
        if not self._seeded or self.zi is None:
            self.zi = self._zi_template * float(x[0])
            self._seeded = True
        y, self.zi = sp_signal.sosfilt(self.sos, x, zi=self.zi)
        return y


class CausalECGPreprocessor:
    """Causal ECG pre-processor: optional 50 Hz notch + custom causal filter stack + QRS bandpass."""

    def __init__(self, fs: float, cfg: PipelineConfig) -> None:
        self.fs = float(fs)
        self.cfg = cfg
        self.notch = (
            CausalSOSFilter(fs, "bandstop", (48.0, 52.0), order=2)
            if cfg.ecg_notch_50hz and fs > 110.0
            else None
        )
        self.disp_chain, self.filter_errors = build_causal_filter_chain(cfg.ecg_filters, fs)
        self.bp_qrs = CausalSOSFilter(
            fs, "bandpass", (cfg.ecg_qrs_low_hz, cfg.ecg_qrs_high_hz), order=cfg.ecg_qrs_order
        )

    def reset(self) -> None:
        if self.notch is not None:
            self.notch.reset()
        for filt in self.disp_chain:
            filt.reset()
        self.bp_qrs.reset()

    def process(self, chunk: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = np.asarray(chunk, dtype=float)
        if len(x) == 0:
            return x.copy(), x.copy()
        y = self.notch.process(x) if self.notch is not None else x.copy()
        for filt in self.disp_chain:
            y = filt.process(y)
        ecg_filtered = y
        ecg_qrs_band = self.bp_qrs.process(ecg_filtered)
        return ecg_filtered, ecg_qrs_band


class CausalEDAPreprocessor:
    """Causal EDA unit converter + custom filter stack + tonic/phasic splitter."""

    def __init__(self, fs: float, cfg: PipelineConfig) -> None:
        self.fs = float(fs)
        self.cfg = cfg
        self.clean_chain, self.filter_errors = build_causal_filter_chain(cfg.eda_filters, fs)
        self.lp_tonic = CausalSOSFilter(fs, "lowpass", cfg.eda_tonic_lp_hz, order=cfg.eda_tonic_order)

    def reset(self) -> None:
        for filt in self.clean_chain:
            filt.reset()
        self.lp_tonic.reset()

    def convert_units(self, chunk: np.ndarray) -> np.ndarray:
        x = np.asarray(chunk, dtype=float)
        val = self.cfg.eda_scale_m * x + self.cfg.eda_offset_b
        if self.cfg.eda_invert:
            with np.errstate(divide="ignore", invalid="ignore"):
                val = np.where(np.abs(val) > 1e-12, self.cfg.eda_invert_num / val, 0.0)
        return val

    def process(self, chunk: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        u = self.convert_units(chunk)
        if len(u) == 0:
            return u.copy(), u.copy(), u.copy()
        y = u
        for filt in self.clean_chain:
            y = filt.process(y)
        eda_filtered = y
        eda_tonic = self.lp_tonic.process(eda_filtered)
        eda_phasic = eda_filtered - eda_tonic
        return eda_filtered, eda_tonic, eda_phasic


class MultiResRingBuffer:
    """Fixed-capacity ring buffer with age-graded decimation for smooth real-time plots."""

    def __init__(
        self,
        window_s: float = 20.0,
        max_fs: float = 512.0,
        mode: str = "lowpass",
    ) -> None:
        self.window_s = max(1.0, float(window_s))
        self.max_fs = max(10.0, float(max_fs))
        self.mode = mode  # "lowpass" or "peak_preserving"
        self._cap = max(256, int(np.ceil(self.window_s * self.max_fs * 1.25)))
        self._t = np.empty(self._cap, dtype=float)
        self._y = np.empty(self._cap, dtype=float)
        self._head = 0
        self._count = 0

    def set_window(self, window_s: float) -> None:
        new_win = max(1.0, float(window_s))
        if abs(new_win - self.window_s) < 1e-6:
            return
        t_cur, y_cur = self._ordered_raw()
        self.window_s = new_win
        self._cap = max(256, int(np.ceil(self.window_s * self.max_fs * 1.25)))
        self._t = np.empty(self._cap, dtype=float)
        self._y = np.empty(self._cap, dtype=float)
        self._head = 0
        self._count = 0
        if len(t_cur) > 0:
            self.append(t_cur, y_cur)

    def clear(self) -> None:
        self._head = 0
        self._count = 0

    def append(self, t_arr: np.ndarray, y_arr: np.ndarray) -> None:
        t = np.asarray(t_arr, dtype=float)
        y = np.asarray(y_arr, dtype=float)
        n = len(t)
        if n == 0:
            return
        if n >= self._cap:
            t = t[-self._cap :]
            y = y[-self._cap :]
            n = self._cap
            self._t[:] = t
            self._y[:] = y
            self._head = 0
            self._count = self._cap
            return

        end = self._head + n
        if end <= self._cap:
            self._t[self._head : end] = t
            self._y[self._head : end] = y
        else:
            first = self._cap - self._head
            self._t[self._head :] = t[:first]
            self._y[self._head :] = y[:first]
            rem = n - first
            self._t[:rem] = t[first:]
            self._y[:rem] = y[first:]
        self._head = end % self._cap
        self._count = min(self._cap, self._count + n)

    def _ordered_raw(self) -> tuple[np.ndarray, np.ndarray]:
        if self._count == 0:
            return np.empty(0, dtype=float), np.empty(0, dtype=float)
        if self._count < self._cap:
            return self._t[: self._count].copy(), self._y[: self._count].copy()
        return (
            np.concatenate([self._t[self._head :], self._t[: self._head]]),
            np.concatenate([self._y[self._head :], self._y[: self._head]]),
        )

    def _decimate_zone(
        self, t_z: np.ndarray, y_z: np.ndarray, factor: int
    ) -> tuple[np.ndarray, np.ndarray]:
        n = len(t_z)
        if factor <= 1 or n < factor * 2:
            return t_z, y_z
        n_blocks = n // factor
        used = n_blocks * factor
        tb = t_z[:used].reshape(n_blocks, factor)
        yb = y_z[:used].reshape(n_blocks, factor)

        if self.mode == "peak_preserving":
            imin = np.argmin(yb, axis=1)
            imax = np.argmax(yb, axis=1)
            first = np.minimum(imin, imax)
            second = np.maximum(imin, imax)
            rows = np.arange(n_blocks)
            out_t = np.empty(n_blocks * 2, dtype=float)
            out_y = np.empty(n_blocks * 2, dtype=float)
            out_t[0::2] = tb[rows, first]
            out_t[1::2] = tb[rows, second]
            out_y[0::2] = yb[rows, first]
            out_y[1::2] = yb[rows, second]
        else:
            out_t = np.mean(tb, axis=1)
            out_y = np.mean(yb, axis=1)

        if used < n:
            rem_t = t_z[used:]
            rem_y = y_z[used:]
            out_t = np.concatenate([out_t, [float(np.mean(rem_t))]])
            out_y = np.concatenate([out_y, [float(np.mean(rem_y))]])
        return out_t, out_y

    def get_display_curve(self) -> tuple[np.ndarray, np.ndarray]:
        t, y = self._ordered_raw()
        if len(t) == 0:
            return t, y
        t_now = float(t[-1])
        t_min = t_now - self.window_s
        i0 = int(np.searchsorted(t, t_min, side="left"))
        t = t[i0:]
        y = y[i0:]
        if len(t) < 32:
            return t, y

        age = t_now - t
        # Oldest -> Newest zones (ordered in time)
        zones = [
            (age > 0.75 * self.window_s, 12),
            ((age > 0.50 * self.window_s) & (age <= 0.75 * self.window_s), 6),
            ((age > 0.25 * self.window_s) & (age <= 0.50 * self.window_s), 3),
            (age <= 0.25 * self.window_s, 1),
        ]
        t_parts: list[np.ndarray] = []
        y_parts: list[np.ndarray] = []
        for mask, factor in zones:
            if not np.any(mask):
                continue
            dt, dy = self._decimate_zone(t[mask], y[mask], factor)
            t_parts.append(dt)
            y_parts.append(dy)

        if not t_parts:
            return np.empty(0, dtype=float), np.empty(0, dtype=float)
        return np.concatenate(t_parts), np.concatenate(y_parts)


class RollingEventWindow:
    """Short rolling event buffer decoupled from the display window."""

    def __init__(self, window_s: float = 10.0) -> None:
        self.window_s = max(1.0, float(window_s))
        self._items: deque[tuple[float, float]] = deque()

    def set_window(self, window_s: float) -> None:
        self.window_s = max(1.0, float(window_s))

    def clear(self) -> None:
        self._items.clear()

    def add(self, t: float, amp: float) -> None:
        self._items.append((float(t), float(amp)))
        self.prune(t)

    def prune(self, t_now: float) -> None:
        cutoff = float(t_now) - self.window_s
        while self._items and self._items[0][0] < cutoff:
            self._items.popleft()

    def recent_times(self) -> np.ndarray:
        if not self._items:
            return np.empty(0, dtype=float)
        return np.array([it[0] for it in self._items], dtype=float)

    def rolling_bpm(self, t_now: float) -> float:
        self.prune(t_now)
        if len(self._items) < 2:
            return 0.0
        times = np.array([it[0] for it in self._items], dtype=float)
        rr = np.diff(times)
        valid = rr[(rr >= 0.28) & (rr <= 2.2)]
        if len(valid) == 0:
            return 0.0
        return float(60.0 / np.median(valid))

    def rolling_rate_per_min(self, t_now: float, elapsed_s: float) -> float:
        self.prune(t_now)
        denom_s = min(self.window_s, max(float(elapsed_s), 5.0))
        return float(len(self._items) * (60.0 / denom_s))


class RollingAmplitudeWindow:
    """Rolling amplitude window independent of the display buffer."""

    def __init__(self, window_s: float = 2.0) -> None:
        self.window_s = max(0.2, float(window_s))
        self._t: deque[float] = deque()
        self._y: deque[float] = deque()

    def clear(self) -> None:
        self._t.clear()
        self._y.clear()

    def append(self, t_arr: np.ndarray, y_arr: np.ndarray) -> None:
        for ti, yi in zip(t_arr, y_arr):
            self._t.append(float(ti))
            self._y.append(float(yi))
        if self._t:
            cutoff = self._t[-1] - self.window_s
            while self._t and self._t[0] < cutoff:
                self._t.popleft()
                self._y.popleft()

    def peak_to_peak(self) -> float:
        if len(self._y) < 2:
            return 0.0
        arr = np.asarray(self._y, dtype=float)
        return float(np.max(arr) - np.min(arr))

    def rms(self) -> float:
        if not self._y:
            return 0.0
        arr = np.asarray(self._y, dtype=float)
        return float(np.sqrt(np.mean(arr * arr)))
