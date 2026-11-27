"""Digital signal processing filter engine (Butterworth, Step, Soft-Knee)."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import signal as sp_signal

from .models import FilterSpec


def default_filter_params(filter_type: str, fs: float) -> dict[str, Any]:
    """Return sensible, Nyquist-safe default parameters for a filter type."""
    nyq = max(fs * 0.5, 0.5)
    lp_default = round(min(40.0, nyq * 0.45), 2)
    hp_default = round(min(0.5, nyq * 0.1), 2)
    bp_low = round(min(5.0, nyq * 0.15), 2)
    bp_high = round(min(35.0, nyq * 0.45), 2)
    if bp_low >= bp_high:
        bp_low = round(nyq * 0.1, 2)
        bp_high = round(nyq * 0.45, 2)
    trans = round(min(4.0, max(0.2, nyq * 0.08)), 2)

    if filter_type == "lowpass":
        return {"cutoff_hz": lp_default, "order": 4, "zero_phase": True}
    if filter_type == "highpass":
        return {"cutoff_hz": hp_default, "order": 4, "zero_phase": True}
    if filter_type == "bandpass":
        return {"low_hz": bp_low, "high_hz": bp_high, "order": 4, "zero_phase": True}
    if filter_type == "bandstop":
        bs_low = round(min(48.0, nyq * 0.35), 2)
        bs_high = round(min(52.0, nyq * 0.45), 2)
        if bs_low >= bs_high:
            bs_low = round(nyq * 0.30, 2)
            bs_high = round(nyq * 0.45, 2)
        return {"low_hz": bs_low, "high_hz": bs_high, "order": 4, "zero_phase": True}
    if filter_type == "step":
        return {
            "mode": "lowpass",
            "cutoff_hz": lp_default,
            "low_hz": bp_low,
            "high_hz": bp_high,
        }
    if filter_type == "knee":
        return {
            "mode": "lowpass",
            "cutoff_hz": lp_default,
            "low_hz": bp_low,
            "high_hz": bp_high,
            "transition_hz": trans,
            "shape": "raised_cosine",
        }
    return {}


def _interpolate_nans(x: np.ndarray) -> np.ndarray:
    if np.all(np.isfinite(x)):
        return x
    out = x.copy()
    finite = np.isfinite(out)
    if not np.any(finite):
        return np.zeros_like(out)
    idx = np.arange(len(out))
    out[~finite] = np.interp(idx[~finite], idx[finite], out[finite])
    return out


def _apply_butterworth(
    x: np.ndarray,
    fs: float,
    btype: str,
    cutoff: float | tuple[float, float],
    order: int,
    zero_phase: bool,
) -> tuple[np.ndarray, str | None]:
    nyq = fs * 0.5
    if nyq <= 0:
        return x.copy(), "Invalid sampling rate."

    order = max(1, min(int(order), 10))
    if isinstance(cutoff, tuple):
        lo, hi = cutoff
        if lo <= 0 or hi <= 0:
            return x.copy(), "Frequencies must be > 0 Hz."
        if lo >= hi:
            return x.copy(), "Low cutoff must be less than high cutoff."
        if hi >= nyq:
            return x.copy(), f"High cutoff ({hi:.2f} Hz) must be < Nyquist ({nyq:.2f} Hz)."
        wn: Any = [lo / nyq, hi / nyq]
    else:
        if cutoff <= 0:
            return x.copy(), "Cutoff frequency must be > 0 Hz."
        if cutoff >= nyq:
            return x.copy(), f"Cutoff ({cutoff:.2f} Hz) must be < Nyquist ({nyq:.2f} Hz)."
        wn = cutoff / nyq

    sos = sp_signal.butter(order, wn, btype=btype, output="sos")
    if zero_phase:
        padlen = 3 * (2 * sos.shape[0] + 1)
        if len(x) <= padlen:
            return sp_signal.sosfilt(sos, x), None
        return sp_signal.sosfiltfilt(sos, x), None
    return sp_signal.sosfilt(sos, x), None


def _transition_curve(t: np.ndarray, shape: str) -> np.ndarray:
    """Map normalized transition coordinate t in [0, 1] from 0 -> 1 smoothly."""
    t_clipped = np.clip(t, 0.0, 1.0)
    if shape == "linear":
        return t_clipped
    if shape == "gaussian":
        # Smooth sigmoid-like Gaussian error ramp normalized to [0, 1]
        z = (t_clipped - 0.5) * 4.0
        g = 1.0 / (1.0 + np.exp(-2.2 * z))
        g0 = 1.0 / (1.0 + np.exp(4.4))
        g1 = 1.0 / (1.0 + np.exp(-4.4))
        return np.clip((g - g0) / (g1 - g0), 0.0, 1.0)
    # Default: raised_cosine
    return 0.5 * (1.0 - np.cos(np.pi * t_clipped))


def _compute_frequency_mask(
    freqs: np.ndarray,
    fs: float,
    mode: str,
    cutoff_hz: float,
    low_hz: float,
    high_hz: float,
    transition_hz: float,
    shape: str,
) -> tuple[np.ndarray | None, str | None]:
    nyq = fs * 0.5
    if nyq <= 0:
        return None, "Invalid sampling rate."

    mode = mode.lower()
    w = max(0.0, float(transition_hz))

    if mode in {"lowpass", "highpass"}:
        fc = float(cutoff_hz)
        if fc < 0 or fc > nyq:
            return None, f"Cutoff ({fc:.2f} Hz) must be between 0 and {nyq:.2f} Hz."
        if w <= 1e-9:
            gain = (freqs <= fc).astype(float) if mode == "lowpass" else (freqs >= fc).astype(float)
        else:
            half = 0.5 * w
            t = (freqs - (fc - half)) / w
            ramp = _transition_curve(t, shape)
            gain = (1.0 - ramp) if mode == "lowpass" else ramp
        return gain, None

    if mode in {"bandpass", "bandstop"}:
        lo = float(low_hz)
        hi = float(high_hz)
        if lo < 0 or hi > nyq or lo >= hi:
            return None, f"Requires 0 <= low < high <= {nyq:.2f} Hz."
        if w <= 1e-9:
            in_band = (freqs >= lo) & (freqs <= hi)
            gain = in_band.astype(float) if mode == "bandpass" else (~in_band).astype(float)
        else:
            half = 0.5 * w
            rising = _transition_curve((freqs - (lo - half)) / w, shape)
            falling = 1.0 - _transition_curve((freqs - (hi - half)) / w, shape)
            bp_gain = np.minimum(rising, falling)
            gain = bp_gain if mode == "bandpass" else (1.0 - bp_gain)
        return gain, None

    return None, f"Unknown frequency filter mode: {mode}"


def _apply_frequency_mask(
    x: np.ndarray,
    fs: float,
    mode: str,
    cutoff_hz: float,
    low_hz: float,
    high_hz: float,
    transition_hz: float,
    shape: str,
) -> tuple[np.ndarray, str | None]:
    if len(x) < 4:
        return x.copy(), "Signal too short or invalid sampling rate."

    freqs = np.fft.rfftfreq(len(x), d=1.0 / fs)
    gain, err = _compute_frequency_mask(
        freqs, fs, mode, cutoff_hz, low_hz, high_hz, transition_hz, shape
    )
    if gain is None:
        return x.copy(), err

    spectrum = np.fft.rfft(x)
    filtered = np.fft.irfft(spectrum * gain, n=len(x))
    return filtered, None


def apply_single_filter(
    signal: np.ndarray,
    fs: float,
    spec: FilterSpec,
) -> tuple[np.ndarray, str | None]:
    """Apply a single FilterSpec to a 1D signal, returning (filtered, error_msg)."""
    if not spec.enabled:
        return signal.copy(), None

    x = _interpolate_nans(np.asarray(signal, dtype=float))
    p = spec.params
    ftype = spec.filter_type.lower()

    if ftype == "lowpass":
        return _apply_butterworth(
            x,
            fs,
            "lowpass",
            float(p.get("cutoff_hz", 40.0)),
            int(p.get("order", 4)),
            bool(p.get("zero_phase", True)),
        )
    if ftype == "highpass":
        return _apply_butterworth(
            x,
            fs,
            "highpass",
            float(p.get("cutoff_hz", 0.5)),
            int(p.get("order", 4)),
            bool(p.get("zero_phase", True)),
        )
    if ftype in {"bandpass", "bandstop"}:
        return _apply_butterworth(
            x,
            fs,
            ftype,
            (float(p.get("low_hz", 5.0)), float(p.get("high_hz", 35.0))),
            int(p.get("order", 4)),
            bool(p.get("zero_phase", True)),
        )
    if ftype == "step":
        return _apply_frequency_mask(
            x,
            fs,
            str(p.get("mode", "lowpass")),
            float(p.get("cutoff_hz", 40.0)),
            float(p.get("low_hz", 5.0)),
            float(p.get("high_hz", 35.0)),
            0.0,
            "raised_cosine",
        )
    if ftype == "knee":
        return _apply_frequency_mask(
            x,
            fs,
            str(p.get("mode", "lowpass")),
            float(p.get("cutoff_hz", 40.0)),
            float(p.get("low_hz", 5.0)),
            float(p.get("high_hz", 35.0)),
            float(p.get("transition_hz", 4.0)),
            str(p.get("shape", "raised_cosine")),
        )
    return x.copy(), f"Unknown filter type: {spec.filter_type}"


def apply_filter_chain(
    raw_signal: np.ndarray,
    fs: float,
    filters: list[FilterSpec],
) -> tuple[np.ndarray, dict[str, str]]:
    """Sequentially apply a stack of filters, each filtering the previous output."""
    current = np.asarray(raw_signal, dtype=float).copy()
    errors: dict[str, str] = {}
    for spec in filters:
        if not spec.enabled:
            continue
        current, err = apply_single_filter(current, fs, spec)
        if err:
            errors[spec.id] = err
    return current, errors


def compute_filter_response(
    freqs: np.ndarray,
    fs: float,
    spec: FilterSpec,
) -> np.ndarray | None:
    """Compute magnitude gain |H(f)| in [0, 1] for a FilterSpec over `freqs`."""
    if not spec.enabled or fs <= 0 or len(freqs) == 0:
        return None

    nyq = fs * 0.5
    p = spec.params
    ftype = spec.filter_type.lower()

    if ftype in {"lowpass", "highpass", "bandpass", "bandstop"}:
        order = max(1, min(int(p.get("order", 4)), 10))
        zero_phase = bool(p.get("zero_phase", True))
        if ftype in {"bandpass", "bandstop"}:
            lo = float(p.get("low_hz", 5.0))
            hi = float(p.get("high_hz", 35.0))
            if lo <= 0 or hi <= 0 or lo >= hi or hi >= nyq:
                return None
            wn: Any = [lo / nyq, hi / nyq]
        else:
            fc = float(p.get("cutoff_hz", 40.0 if ftype == "lowpass" else 0.5))
            if fc <= 0 or fc >= nyq:
                return None
            wn = fc / nyq
        sos = sp_signal.butter(order, wn, btype=ftype, output="sos")
        _, h = sp_signal.sosfreqz(sos, worN=freqs, fs=fs)
        mag = np.abs(h)
        return (mag * mag) if zero_phase else mag

    if ftype == "step":
        gain, err = _compute_frequency_mask(
            freqs,
            fs,
            str(p.get("mode", "lowpass")),
            float(p.get("cutoff_hz", 40.0)),
            float(p.get("low_hz", 5.0)),
            float(p.get("high_hz", 35.0)),
            0.0,
            "raised_cosine",
        )
        return gain if err is None else None

    if ftype == "knee":
        gain, err = _compute_frequency_mask(
            freqs,
            fs,
            str(p.get("mode", "lowpass")),
            float(p.get("cutoff_hz", 40.0)),
            float(p.get("low_hz", 5.0)),
            float(p.get("high_hz", 35.0)),
            float(p.get("transition_hz", 4.0)),
            str(p.get("shape", "raised_cosine")),
        )
        return gain if err is None else None

    return None


def compute_combined_filter_response(
    freqs: np.ndarray,
    fs: float,
    filters: list[FilterSpec],
) -> tuple[list[tuple[FilterSpec, np.ndarray]], np.ndarray | None]:
    """Compute per-filter responses and their cascaded product (when 2+ active)."""
    responses: list[tuple[FilterSpec, np.ndarray]] = []
    for spec in filters:
        mag = compute_filter_response(freqs, fs, spec)
        if mag is not None:
            responses.append((spec, mag))

    combined: np.ndarray | None = None
    if len(responses) >= 2:
        combined = np.ones_like(freqs, dtype=float)
        for _, mag in responses:
            combined *= mag
    return responses, combined


def compute_power_spectrum(
    signal: np.ndarray,
    fs: float,
    method: str = "welch",
    use_db: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute single-sided PSD (Welch or Hann-windowed FFT periodogram)."""
    x = _interpolate_nans(np.asarray(signal, dtype=float))
    if len(x) < 8 or fs <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)

    method = method.lower()
    if method == "fft":
        xc = x - float(np.mean(x))
        win = np.hanning(len(xc))
        norm = float(np.sum(win * win)) or 1.0
        spec = np.fft.rfft(xc * win)
        psd = (np.abs(spec) ** 2) / (fs * norm)
        if len(psd) > 2:
            psd[1:-1] *= 2.0
        elif len(psd) == 2 and len(xc) % 2 == 1:
            psd[1] *= 2.0
        freqs = np.fft.rfftfreq(len(xc), d=1.0 / fs)
    else:
        nperseg = min(len(x), max(256, int(round(fs * 4.0))))
        if nperseg > len(x):
            nperseg = len(x)
        freqs, psd = sp_signal.welch(
            x, fs=fs, nperseg=nperseg, detrend="constant"
        )

    if use_db:
        psd_out = 10.0 * np.log10(np.maximum(psd, 1e-20))
    else:
        psd_out = psd
    return freqs, psd_out


def downsample_for_viewport(
    x: np.ndarray,
    y: np.ndarray,
    x_min: float | None,
    x_max: float | None,
    width_px: float,
    device_pixel_ratio: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Clip to visible X range and apply peak-preserving min-max envelope downsampling.

    Keeps full native resolution whenever visible sample count <= 2 * physical pixels.
    """
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    n = len(x_arr)
    if n <= 2 or len(y_arr) != n:
        return x_arr, y_arr

    if (
        x_min is not None
        and x_max is not None
        and np.isfinite(x_min)
        and np.isfinite(x_max)
        and x_max > x_min
        and x_arr[0] <= x_arr[-1]
    ):
        i0 = int(np.searchsorted(x_arr, x_min, side="left"))
        i1 = int(np.searchsorted(x_arr, x_max, side="right"))
        i0 = max(0, i0 - 1)
        i1 = min(n, i1 + 1)
        if i1 - i0 < 2:
            i0 = max(0, min(n - 2, i0))
            i1 = min(n, i0 + 2)
        x_vis = x_arr[i0:i1]
        y_vis = y_arr[i0:i1]
    else:
        x_vis = x_arr
        y_vis = y_arr

    m = len(x_vis)
    effective_px = max(64, int(round(max(1.0, float(width_px)) * max(1.0, float(device_pixel_ratio)))))
    if m <= 2 * effective_px:
        return x_vis, y_vis

    n_bins = effective_px
    bucket = m // n_bins
    if bucket < 2:
        return x_vis, y_vis

    n_used = n_bins * bucket
    x_trim = x_vis[:n_used].reshape(n_bins, bucket)
    y_trim = y_vis[:n_used].reshape(n_bins, bucket)

    if np.all(np.isfinite(y_trim)):
        imin = np.argmin(y_trim, axis=1)
        imax = np.argmax(y_trim, axis=1)
    else:
        imin = np.zeros(n_bins, dtype=int)
        imax = np.zeros(n_bins, dtype=int)
        for b in range(n_bins):
            row = y_trim[b]
            if np.any(np.isfinite(row)):
                imin[b] = int(np.nanargmin(row))
                imax[b] = int(np.nanargmax(row))

    first = np.minimum(imin, imax)
    second = np.maximum(imin, imax)
    rows = np.arange(n_bins)

    out_x = np.empty(n_bins * 2, dtype=float)
    out_y = np.empty(n_bins * 2, dtype=float)
    out_x[0::2] = x_trim[rows, first]
    out_x[1::2] = x_trim[rows, second]
    out_y[0::2] = y_trim[rows, first]
    out_y[1::2] = y_trim[rows, second]

    if n_used < m:
        rem_x = x_vis[n_used:]
        rem_y = y_vis[n_used:]
        if np.any(np.isfinite(rem_y)):
            ri_min = int(np.nanargmin(rem_y))
            ri_max = int(np.nanargmax(rem_y))
            r_first, r_second = min(ri_min, ri_max), max(ri_min, ri_max)
            tail_idx = [r_first] if r_first == r_second else [r_first, r_second]
            out_x = np.concatenate([out_x, rem_x[tail_idx]])
            out_y = np.concatenate([out_y, rem_y[tail_idx]])

    return out_x, out_y


def evaluate_linear_combination(
    raw_data: np.ndarray,
    filtered_signals: list[np.ndarray],
    spec: dict[str, Any],
) -> np.ndarray:
    """Compute a derived channel as an affine linear combination (with optional reciprocal)."""
    n_samples, n_channels = raw_data.shape
    result = np.full(n_samples, float(spec.get("offset", 0.0)), dtype=float)
    for term in spec.get("terms", []):
        idx = int(term.get("channel_index", -1))
        if not (0 <= idx < n_channels):
            continue
        weight = float(term.get("weight", 0.0))
        source = str(term.get("source", "raw")).lower()
        if source == "filtered" and idx < len(filtered_signals):
            src_arr = filtered_signals[idx]
        else:
            src_arr = raw_data[:, idx]
        result = result + weight * np.asarray(src_arr, dtype=float)

    if bool(spec.get("invert", False)):
        num = float(spec.get("invert_numerator", 1.0))
        with np.errstate(divide="ignore", invalid="ignore"):
            result = np.where(np.abs(result) > 1e-12, num / result, np.nan)
    return result


def format_linear_combination_formula(
    spec: dict[str, Any],
    channel_names: list[str],
    unit: str = "",
) -> str:
    """Return a concise human-readable representation of a linear combination spec."""
    parts: list[str] = []
    for term in spec.get("terms", []):
        idx = int(term.get("channel_index", -1))
        w = float(term.get("weight", 0.0))
        src = str(term.get("source", "raw")).lower()
        ch_lbl = channel_names[idx] if 0 <= idx < len(channel_names) else f"Ch{idx + 1}"
        tag = f"{ch_lbl}[{src}]"
        parts.append(f"{w:g}·{tag}")

    offset = float(spec.get("offset", 0.0))
    expr = " + ".join(parts) if parts else "0"
    if abs(offset) > 0.0 or not parts:
        sign = "+" if offset >= 0 else "-"
        if parts:
            expr = f"{expr} {sign} {abs(offset):g}"
        else:
            expr = f"{offset:g}"

    if bool(spec.get("invert", False)):
        num = float(spec.get("invert_numerator", 1.0))
        expr = f"{num:g} / ({expr})"

    u = unit.strip()
    return f"{expr}  [{u}]" if u else expr
