"""Calibrated Bayesian prior-blended template event detectors for ECG R-peaks & EDA SCRs."""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import signal as sp_signal

from .models import DetectedEvent, ECGCalibration, EDACalibration


def _normalize_template(arr: np.ndarray) -> np.ndarray:
    a = np.asarray(arr, dtype=float)
    a = a - float(np.mean(a))
    norm = float(np.linalg.norm(a))
    if norm < 1e-12:
        out = np.zeros_like(a)
        out[len(out) // 2] = 1.0
        return out
    return a / norm


def _sliding_normalized_xcorr(x: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Compute Pearson correlation r in [-1, 1] between sliding windows of x and template."""
    L = len(template)
    if len(x) < L or L < 3:
        return np.empty(0, dtype=float)
    windows = sliding_window_view(x, L)
    w_mean = np.mean(windows, axis=1, keepdims=True)
    w_centered = windows - w_mean
    w_norm = np.sqrt(np.sum(w_centered * w_centered, axis=1))
    dots = w_centered @ template
    return np.where(w_norm > 1e-9, dots / w_norm, 0.0)


def make_qrs_prior(fs: float, width_s: float = 0.09) -> np.ndarray:
    """Return an odd-length canonical Q-R-S biphasic/triphasic template prior."""
    n = max(7, int(round(width_s * fs)))
    if n % 2 == 0:
        n += 1
    t = np.linspace(-1.0, 1.0, n)
    # R-peak positive lobe flanked by smaller Q/S negative dips
    r_wave = np.exp(-((t / 0.24) ** 2))
    q_dip = -0.25 * np.exp(-(((t + 0.42) / 0.16) ** 2))
    s_dip = -0.30 * np.exp(-(((t - 0.42) / 0.18) ** 2))
    return _normalize_template(q_dip + r_wave + s_dip)


def make_scr_prior(fs_internal: float = 10.0, width_s: float = 4.0) -> np.ndarray:
    """Return an odd-length canonical Bateman SCR impulse response prior."""
    n = max(11, int(round(width_s * fs_internal)))
    if n % 2 == 0:
        n += 1
    t = np.linspace(0.0, width_s, n)
    # Shift onset slightly into window so rise and early recovery sit cleanly inside template
    tau_rise = 0.75
    tau_rec = 2.5
    t_eff = np.maximum(0.0, t - 0.25 * width_s * 0.2)
    bateman = np.exp(-t_eff / tau_rec) - np.exp(-t_eff / tau_rise)
    bateman[t < 0.2] = 0.0
    return _normalize_template(bateman)


class ECGRPeakTemplateDetector:
    """Calibrated matched-filter R-peak detector with Bayesian prior blending."""

    def __init__(self, fs: float = 250.0) -> None:
        self.fs = max(10.0, float(fs))
        prior = make_qrs_prior(self.fs)
        self.calibration = ECGCalibration(
            fs=self.fs,
            template=prior.tolist(),
            corr_threshold=0.70,
            amp_threshold=0.10,
            polarity=1,
            refractory_s=0.30,
            n_beats_used=0,
            noise_mad=0.0,
            calibrated=False,
        )
        self._template = prior
        self._last_peak_t = -1e9
        self._tail_t = np.empty(0, dtype=float)
        self._tail_lsl = np.empty(0, dtype=float)
        self._tail_qrs = np.empty(0, dtype=float)
        self._tail_filt = np.empty(0, dtype=float)

    def reset_state(self) -> None:
        self._last_peak_t = -1e9
        self._tail_t = np.empty(0, dtype=float)
        self._tail_lsl = np.empty(0, dtype=float)
        self._tail_qrs = np.empty(0, dtype=float)
        self._tail_filt = np.empty(0, dtype=float)

    def apply_calibration(self, cal: ECGCalibration) -> None:
        self.calibration = cal
        if cal.template:
            self._template = _normalize_template(np.asarray(cal.template, dtype=float))
        else:
            self._template = make_qrs_prior(self.fs)
        self.reset_state()

    def calibrate(
        self,
        t_cal: np.ndarray,
        y_qrs_cal: np.ndarray,
        fs: float,
        prior_weight: float = 5.0,
    ) -> ECGCalibration:
        self.fs = max(10.0, float(fs))
        prior = make_qrs_prior(self.fs)
        L = len(prior)
        half = L // 2

        y = np.asarray(y_qrs_cal, dtype=float)
        t = np.asarray(t_cal, dtype=float)
        if len(y) < max(L * 3, int(self.fs * 2.0)):
            cal = ECGCalibration(
                fs=self.fs,
                template=prior.tolist(),
                corr_threshold=0.70,
                amp_threshold=max(0.05, 0.25 * float(np.std(y)) if len(y) else 0.1),
                polarity=1,
                refractory_s=0.30,
                n_beats_used=0,
                noise_mad=0.0,
                calibrated=True,
            )
            self.apply_calibration(cal)
            return cal

        p995 = float(np.percentile(y, 99.5))
        p005 = float(np.percentile(y, 0.5))
        polarity = 1 if abs(p995) >= abs(p005) else -1
        y_pol = polarity * y

        med = float(np.median(y_pol))
        mad = float(np.median(np.abs(y_pol - med))) * 1.4826

        # Pan-Tompkins-style energy envelope to locate candidate beats
        deriv = np.gradient(y_pol)
        sq = deriv * deriv
        win = max(3, int(round(0.12 * self.fs)))
        mwi = np.convolve(sq, np.ones(win, dtype=float) / win, mode="same")
        dist = max(1, int(round(0.30 * self.fs)))
        prom = max(float(np.percentile(mwi, 85)) * 0.25, 1e-12)
        cand_peaks, _ = sp_signal.find_peaks(mwi, distance=dist, prominence=prom)

        search_r = max(2, int(round(0.06 * self.fs)))
        refined: list[int] = []
        for idx in cand_peaks:
            i0 = max(0, idx - search_r)
            i1 = min(len(y_pol), idx + search_r + 1)
            local_idx = i0 + int(np.argmax(y_pol[i0:i1]))
            if not refined or (local_idx - refined[-1]) >= dist:
                refined.append(local_idx)

        snippets: list[np.ndarray] = []
        beat_amps: list[float] = []
        for idx in refined:
            if idx - half < 0 or idx + half + 1 > len(y_pol):
                continue
            snip = _normalize_template(y_pol[idx - half : idx + half + 1])
            snippets.append(snip)
            beat_amps.append(float(y_pol[idx]))

        n_beats = len(snippets)
        if n_beats > 0:
            mean_snip = np.mean(np.stack(snippets, axis=0), axis=0)
            blended = (prior_weight * prior + float(n_beats) * mean_snip) / (
                prior_weight + float(n_beats)
            )
            learned_tmpl = _normalize_template(blended)
            med_amp = float(np.median(beat_amps))
            amp_thresh = max(0.35 * med_amp, 3.5 * mad)

            # Score calibration data with learned template to auto-set corr_threshold
            r_vals = _sliding_normalized_xcorr(y_pol, learned_tmpl)
            beat_corrs: list[float] = []
            for idx in refined:
                c_idx = idx - half
                if 0 <= c_idx < len(r_vals):
                    beat_corrs.append(float(r_vals[c_idx]))
            if beat_corrs:
                p20 = float(np.percentile(beat_corrs, 20))
                corr_thresh = float(np.clip(min(0.84, max(0.55, p20 * 0.88)), 0.55, 0.88))
            else:
                corr_thresh = 0.70
        else:
            learned_tmpl = prior
            amp_thresh = max(4.0 * mad, 0.1)
            corr_thresh = 0.70

        cal = ECGCalibration(
            fs=self.fs,
            template=learned_tmpl.tolist(),
            corr_threshold=corr_thresh,
            amp_threshold=float(amp_thresh),
            polarity=polarity,
            refractory_s=0.30,
            n_beats_used=n_beats,
            noise_mad=float(mad),
            calibrated=True,
        )
        self.apply_calibration(cal)
        return cal

    def process_chunk(
        self,
        t_chunk: np.ndarray,
        y_qrs_chunk: np.ndarray,
        y_filt_chunk: np.ndarray,
        lsl_t_chunk: np.ndarray,
        online_adapt: bool = False,
    ) -> tuple[list[DetectedEvent], np.ndarray]:
        t_c = np.asarray(t_chunk, dtype=float)
        q_c = np.asarray(y_qrs_chunk, dtype=float)
        f_c = np.asarray(y_filt_chunk, dtype=float)
        l_c = np.asarray(lsl_t_chunk, dtype=float)
        if len(t_c) == 0:
            return [], np.empty(0, dtype=float)

        L = len(self._template)
        half = L // 2

        t_buf = np.concatenate([self._tail_t, t_c])
        l_buf = np.concatenate([self._tail_lsl, l_c])
        q_buf = np.concatenate([self._tail_qrs, q_c])
        f_buf = np.concatenate([self._tail_filt, f_c])

        # Keep L + 1 overlap samples so even a 1-sample chunk produces len(r_vals) >= 3
        keep = L + 1
        self._tail_t = t_buf[-keep:].copy()
        self._tail_lsl = l_buf[-keep:].copy()
        self._tail_qrs = q_buf[-keep:].copy()
        self._tail_filt = f_buf[-keep:].copy()

        if len(q_buf) < L + 2:
            return [], np.zeros(len(t_c), dtype=float)

        pol = 1 if self.calibration.polarity >= 0 else -1
        q_pol = pol * q_buf
        r_vals = _sliding_normalized_xcorr(q_pol, self._template)
        if len(r_vals) < 3:
            return [], np.zeros(len(t_c), dtype=float)

        events: list[DetectedEvent] = []
        # Find local maxima of r_vals (center index i corresponds to buf[i + half])
        is_local_max = (r_vals[1:-1] >= r_vals[:-2]) & (r_vals[1:-1] > r_vals[2:])
        cand_r_indices = np.where(is_local_max)[0] + 1

        for r_idx in cand_r_indices:
            center = int(r_idx) + half
            r_score = float(r_vals[r_idx])
            if r_score < self.calibration.corr_threshold:
                continue
            amp = float(q_pol[center])
            if amp < self.calibration.amp_threshold:
                continue
            ev_t = float(t_buf[center])
            if (ev_t - self._last_peak_t) < self.calibration.refractory_s:
                continue

            self._last_peak_t = ev_t
            events.append(
                DetectedEvent(
                    kind="R_PEAK",
                    time_s=ev_t,
                    lsl_time=float(l_buf[center]),
                    amplitude=float(f_buf[center]),
                    confidence=r_score,
                    extra={"qrs_amp": amp},
                )
            )
            if online_adapt and r_score >= 0.90:
                snip = _normalize_template(q_pol[center - half : center + half + 1])
                self._template = _normalize_template(0.96 * self._template + 0.04 * snip)
                self.calibration.template = self._template.tolist()

        # Return correlation tail aligned to t_c
        r_tail = r_vals[-len(t_c) :] if len(r_vals) >= len(t_c) else np.pad(
            r_vals, (len(t_c) - len(r_vals), 0)
        )
        return events, r_tail


class EDASCRTemplateDetector:
    """Calibrated matched-filter EDA SCR detector operating at 10 Hz internal rate."""

    def __init__(self, fs_internal: float = 10.0) -> None:
        self.fs_internal = float(fs_internal)
        prior = make_scr_prior(self.fs_internal)
        self.calibration = EDACalibration(
            fs_internal=self.fs_internal,
            template=prior.tolist(),
            corr_threshold=0.60,
            amp_threshold=0.05,
            refractory_s=2.0,
            n_events_used=0,
            noise_mad=0.0,
            calibrated=False,
        )
        self._template = prior
        self._last_scr_t = -1e9
        self._ds_dt = 1.0 / self.fs_internal
        self._next_bin_t: float | None = None
        self._bin_t: list[float] = []
        self._bin_lsl: list[float] = []
        self._bin_y: list[float] = []
        self._tail_t = np.empty(0, dtype=float)
        self._tail_lsl = np.empty(0, dtype=float)
        self._tail_y = np.empty(0, dtype=float)

    def reset_state(self) -> None:
        self._last_scr_t = -1e9
        self._next_bin_t = None
        self._bin_t.clear()
        self._bin_lsl.clear()
        self._bin_y.clear()
        self._tail_t = np.empty(0, dtype=float)
        self._tail_lsl = np.empty(0, dtype=float)
        self._tail_y = np.empty(0, dtype=float)

    def apply_calibration(self, cal: EDACalibration) -> None:
        self.calibration = cal
        if cal.template:
            self._template = _normalize_template(np.asarray(cal.template, dtype=float))
        else:
            self._template = make_scr_prior(self.fs_internal)
        self.reset_state()

    def _downsample_array(
        self, t_arr: np.ndarray, y_arr: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        if len(t_arr) == 0:
            return np.empty(0, dtype=float), np.empty(0, dtype=float)
        t0 = float(t_arr[0])
        t1 = float(t_arr[-1])
        if t1 - t0 < self._ds_dt:
            return np.array([float(np.mean(t_arr))]), np.array([float(np.mean(y_arr))])
        edges = np.arange(t0, t1 + self._ds_dt, self._ds_dt)
        t_out: list[float] = []
        y_out: list[float] = []
        idx0 = 0
        for e in edges[1:]:
            idx1 = int(np.searchsorted(t_arr, e, side="left"))
            if idx1 > idx0:
                t_out.append(float(np.mean(t_arr[idx0:idx1])))
                y_out.append(float(np.mean(y_arr[idx0:idx1])))
            idx0 = idx1
        return np.asarray(t_out, dtype=float), np.asarray(y_out, dtype=float)

    def calibrate(
        self,
        t_cal: np.ndarray,
        y_phasic_cal: np.ndarray,
        fs: float,  # kept for symmetric API
        prior_weight: float = 3.0,
    ) -> EDACalibration:
        prior = make_scr_prior(self.fs_internal)
        L = len(prior)
        half = L // 2

        t_ds, y_ds = self._downsample_array(
            np.asarray(t_cal, dtype=float), np.asarray(y_phasic_cal, dtype=float)
        )
        if len(y_ds) < max(L + 4, 20):
            cal = EDACalibration(
                fs_internal=self.fs_internal,
                template=prior.tolist(),
                corr_threshold=0.60,
                amp_threshold=0.05,
                refractory_s=2.0,
                n_events_used=0,
                noise_mad=0.0,
                calibrated=True,
            )
            self.apply_calibration(cal)
            return cal

        # Estimate sample-to-sample noise floor from first differences so real SCR waves
        # do not inflate the threshold.
        diffs = np.diff(y_ds)
        diff_med = float(np.median(diffs))
        mad = float(np.median(np.abs(diffs - diff_med))) * 1.4826 / np.sqrt(2.0)
        amp_thresh = max(0.05, 4.0 * mad)

        dist = max(1, int(round(2.0 * self.fs_internal)))
        cand_peaks, _ = sp_signal.find_peaks(
            y_ds, height=amp_thresh, distance=dist, prominence=max(amp_thresh * 0.5, 0.04)
        )

        snippets: list[np.ndarray] = []
        for idx in cand_peaks:
            # Align template window around the peak (BATEMAN peak sits around ~40% of window)
            center_shift = int(round(0.1 * L))
            start = idx - half + center_shift
            end = start + L
            if start < 0 or end > len(y_ds):
                continue
            snippets.append(_normalize_template(y_ds[start:end]))

        n_ev = len(snippets)
        if n_ev > 0:
            mean_snip = np.mean(np.stack(snippets, axis=0), axis=0)
            blended = (prior_weight * prior + float(n_ev) * mean_snip) / (
                prior_weight + float(n_ev)
            )
            learned_tmpl = _normalize_template(blended)
            r_vals = _sliding_normalized_xcorr(y_ds, learned_tmpl)
            corrs: list[float] = []
            for idx in cand_peaks:
                r_i = idx - half + int(round(0.1 * L))
                if 0 <= r_i < len(r_vals):
                    corrs.append(float(r_vals[r_i]))
            if corrs:
                corr_thresh = float(np.clip(np.percentile(corrs, 20) * 0.85, 0.50, 0.80))
            else:
                corr_thresh = 0.60
        else:
            learned_tmpl = prior
            corr_thresh = 0.60

        cal = EDACalibration(
            fs_internal=self.fs_internal,
            template=learned_tmpl.tolist(),
            corr_threshold=corr_thresh,
            amp_threshold=float(amp_thresh),
            refractory_s=2.0,
            n_events_used=n_ev,
            noise_mad=float(mad),
            calibrated=True,
        )
        self.apply_calibration(cal)
        return cal

    def process_chunk(
        self,
        t_chunk: np.ndarray,
        y_phasic_chunk: np.ndarray,
        lsl_t_chunk: np.ndarray,
        online_adapt: bool = False,
    ) -> tuple[list[DetectedEvent], np.ndarray]:
        t_c = np.asarray(t_chunk, dtype=float)
        y_c = np.asarray(y_phasic_chunk, dtype=float)
        l_c = np.asarray(lsl_t_chunk, dtype=float)
        if len(t_c) == 0:
            return [], np.empty(0, dtype=float)

        ds_t: list[float] = []
        ds_l: list[float] = []
        ds_y: list[float] = []

        for ti, li, yi in zip(t_c, l_c, y_c):
            if self._next_bin_t is None:
                self._next_bin_t = float(ti) + self._ds_dt
            self._bin_t.append(float(ti))
            self._bin_lsl.append(float(li))
            self._bin_y.append(float(yi))
            if float(ti) >= self._next_bin_t:
                ds_t.append(float(np.mean(self._bin_t)))
                ds_l.append(float(np.mean(self._bin_lsl)))
                ds_y.append(float(np.mean(self._bin_y)))
                self._bin_t.clear()
                self._bin_lsl.clear()
                self._bin_y.clear()
                while self._next_bin_t <= float(ti):
                    self._next_bin_t += self._ds_dt

        if not ds_t:
            return [], np.empty(0, dtype=float)

        t_new = np.asarray(ds_t, dtype=float)
        l_new = np.asarray(ds_l, dtype=float)
        y_new = np.asarray(ds_y, dtype=float)

        L = len(self._template)
        half = L // 2

        t_buf = np.concatenate([self._tail_t, t_new])
        l_buf = np.concatenate([self._tail_lsl, l_new])
        y_buf = np.concatenate([self._tail_y, y_new])

        # Keep L + 1 overlap samples so even a 1-sample downsampled chunk produces len(r_vals) >= 3
        keep = L + 1
        self._tail_t = t_buf[-keep:].copy()
        self._tail_lsl = l_buf[-keep:].copy()
        self._tail_y = y_buf[-keep:].copy()

        if len(y_buf) < L + 2:
            return [], np.zeros(len(t_new), dtype=float)

        r_vals = _sliding_normalized_xcorr(y_buf, self._template)
        if len(r_vals) < 3:
            return [], np.zeros(len(t_new), dtype=float)

        events: list[DetectedEvent] = []
        is_local_max = (r_vals[1:-1] >= r_vals[:-2]) & (r_vals[1:-1] > r_vals[2:])
        cand_r_indices = np.where(is_local_max)[0] + 1

        for r_idx in cand_r_indices:
            win_start = int(r_idx)
            win_end = win_start + L
            local_peak_off = int(np.argmax(y_buf[win_start:win_end]))
            center = win_start + local_peak_off
            r_score = float(r_vals[r_idx])
            if r_score < self.calibration.corr_threshold:
                continue
            amp = float(y_buf[center])
            if amp < self.calibration.amp_threshold:
                continue
            ev_t = float(t_buf[center])
            if (ev_t - self._last_scr_t) < self.calibration.refractory_s:
                continue

            self._last_scr_t = ev_t
            events.append(
                DetectedEvent(
                    kind="SCR",
                    time_s=ev_t,
                    lsl_time=float(l_buf[center]),
                    amplitude=amp,
                    confidence=r_score,
                )
            )
            if online_adapt and r_score >= 0.88:
                snip = _normalize_template(y_buf[win_start:win_end])
                self._template = _normalize_template(0.95 * self._template + 0.05 * snip)
                self.calibration.template = self._template.tolist()

        return events, r_vals[-len(t_new) :]
