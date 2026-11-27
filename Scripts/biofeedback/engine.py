"""Thread-safe, GUI-independent real-time biosignal processing & LSL broadcast engine."""

from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from .detectors import ECGRPeakTemplateDetector, EDASCRTemplateDetector
from .dsp_realtime import (
    CausalECGPreprocessor,
    CausalEDAPreprocessor,
    MultiResRingBuffer,
    RollingAmplitudeWindow,
    RollingEventWindow,
)
from .lsl_io import BiofeedbackLSLOutlets, LSLInputSource, SyntheticBioSource
from .models import (
    DetectedEvent,
    ECGCalibration,
    EDACalibration,
    FeatureSnapshot,
    PipelineConfig,
)


class BiofeedbackEngine:
    """Thread-safe real-time DSP, template detection, calibration, and LSL broadcast engine."""

    def __init__(self, cfg: PipelineConfig | None = None) -> None:
        self.cfg: PipelineConfig = cfg if cfg is not None else PipelineConfig()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

        self._source: LSLInputSource | SyntheticBioSource | None = None
        self._outlets: BiofeedbackLSLOutlets | None = None
        self._fs: float = 250.0
        self._running: bool = False
        self.last_error: str | None = None

        self.ecg_ch_idx: int = 0
        self.eda_ch_idx: int = 1
        self.record_signal_buffers: bool = True
        self.record_feature_buffers: bool = True
        self.broadcast_lsl: bool = True
        self.total_samples_processed: int = 0
        self._raw_publish_labels: list[str] | None = None

        # Preprocessors & detectors
        self._ecg_pre = CausalECGPreprocessor(self._fs, self.cfg)
        self._eda_pre = CausalEDAPreprocessor(self._fs, self.cfg)
        self._ecg_det = ECGRPeakTemplateDetector(self._fs)
        self._eda_det = EDASCRTemplateDetector(fs_internal=10.0)

        # Decoupled display buffers vs. short rolling stats
        self._ecg_disp = MultiResRingBuffer(self.cfg.display_window_s, max_fs=512.0, mode="peak_preserving")
        self._eda_disp = MultiResRingBuffer(self.cfg.display_window_s, max_fs=512.0, mode="lowpass")
        self._eda_tonic_disp = MultiResRingBuffer(self.cfg.display_window_s, max_fs=512.0, mode="lowpass")
        self._rpeak_win = RollingEventWindow(self.cfg.bpm_window_s)
        self._scr_win = RollingEventWindow(self.cfg.scr_window_s)
        self._ecg_amp_win = RollingAmplitudeWindow(self.cfg.amp_window_s)

        self._disp_rpeaks: deque[tuple[float, float]] = deque()
        self._disp_scrs: deque[tuple[float, float]] = deque()

        # Calibration collection state
        self._calibrating: bool = False
        self._cal_start_t: float | None = None
        self._cal_progress_pct: int = 0
        self._cal_summary: str = ""
        self.calibration_version: int = 0
        self._cal_ecg_t: list[np.ndarray] = []
        self._cal_ecg_y: list[np.ndarray] = []
        self._cal_eda_t: list[np.ndarray] = []
        self._cal_eda_y: list[np.ndarray] = []

        self._t_latest: float = 0.0
        self._last_snapshot = FeatureSnapshot()
        self._last_event: DetectedEvent | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def fs(self) -> float:
        with self._lock:
            return self._fs

    @property
    def record_display_buffers(self) -> bool:
        with self._lock:
            return self.record_signal_buffers or self.record_feature_buffers

    def start(
        self,
        source: LSLInputSource | SyntheticBioSource,
        fs: float,
        ecg_ch_idx: int,
        eda_ch_idx: int,
        broadcast_lsl: bool = True,
        start_thread: bool = True,
    ) -> None:
        """Start streaming from `source`, optionally launching the background worker thread."""
        self.stop()
        with self._lock:
            self._source = source
            self._fs = float(fs) if fs > 0 else 250.0
            self.ecg_ch_idx = int(ecg_ch_idx)
            self.eda_ch_idx = int(eda_ch_idx)
            self.broadcast_lsl = bool(broadcast_lsl)
            self.last_error = None
            self.total_samples_processed = 0

            self._ecg_pre = CausalECGPreprocessor(self._fs, self.cfg)
            self._eda_pre = CausalEDAPreprocessor(self._fs, self.cfg)
            if not self._ecg_det.calibration.calibrated:
                self._ecg_det = ECGRPeakTemplateDetector(self._fs)
            else:
                self._ecg_det.reset_state()
            self._eda_det.reset_state()

            self._clear_display_buffers_locked()
            self._rpeak_win.clear()
            self._scr_win.clear()
            self._ecg_amp_win.clear()
            self._t_latest = 0.0
            self._last_snapshot = FeatureSnapshot()
            self._last_event = None
            self._calibrating = False
            self._cal_progress_pct = 0

            self._raw_publish_labels = (
                list(source.channel_labels) if isinstance(source, SyntheticBioSource) else None
            )
            if self.broadcast_lsl:
                self._outlets = BiofeedbackLSLOutlets(
                    feature_rate_hz=25.0,
                    sample_rate_hz=self._fs,
                    raw_channel_labels=self._raw_publish_labels,
                )
            else:
                self._outlets = None

            self._stop_event.clear()
            self._running = True
            if start_thread:
                self._worker_thread = threading.Thread(
                    target=self._worker_loop,
                    name="BiofeedbackDSPWorker",
                    daemon=True,
                )
                self._worker_thread.start()

    def stop(self) -> None:
        """Signal the worker thread to exit, wait for join, and release streams cleanly."""
        self._stop_event.set()
        worker: threading.Thread | None
        with self._lock:
            worker = self._worker_thread
            self._worker_thread = None

        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=2.0)

        with self._lock:
            self._running = False
            self._calibrating = False
            if self._source is not None:
                try:
                    self._source.close()
                finally:
                    self._source = None
            if self._outlets is not None:
                try:
                    self._outlets.close()
                finally:
                    self._outlets = None

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.step_once()
            except Exception as exc:
                with self._lock:
                    self.last_error = str(exc)
                    self._running = False
                break
            self._stop_event.wait(0.008)

    def _clear_display_buffers_locked(self) -> None:
        self._ecg_disp.clear()
        self._eda_disp.clear()
        self._eda_tonic_disp.clear()
        self._disp_rpeaks.clear()
        self._disp_scrs.clear()

    def _trim_display_events_locked(self) -> None:
        cutoff = self._t_latest - self.cfg.display_window_s
        while self._disp_rpeaks and self._disp_rpeaks[0][0] < cutoff:
            self._disp_rpeaks.popleft()
        while self._disp_scrs and self._disp_scrs[0][0] < cutoff:
            self._disp_scrs.popleft()

    def set_display_recording(self, signals: bool, features: bool) -> None:
        with self._lock:
            new_sig = bool(signals)
            new_feat = bool(features)
            if new_sig and not self.record_signal_buffers:
                self._ecg_disp.clear()
                self._eda_disp.clear()
            if new_feat and not self.record_feature_buffers:
                self._eda_tonic_disp.clear()
                self._disp_rpeaks.clear()
                self._disp_scrs.clear()
            self.record_signal_buffers = new_sig
            self.record_feature_buffers = new_feat

    def set_record_display_buffers(self, enabled: bool) -> None:
        val = bool(enabled)
        self.set_display_recording(val, val)

    def set_broadcast_lsl(self, enabled: bool) -> None:
        with self._lock:
            self.broadcast_lsl = bool(enabled)
            if self._running:
                if self.broadcast_lsl and self._outlets is None:
                    self._outlets = BiofeedbackLSLOutlets(
                        feature_rate_hz=25.0,
                        sample_rate_hz=self._fs,
                        raw_channel_labels=self._raw_publish_labels,
                    )
                elif not self.broadcast_lsl and self._outlets is not None:
                    self._outlets.close()
                    self._outlets = None

    def set_channels(self, ecg_ch_idx: int, eda_ch_idx: int) -> None:
        with self._lock:
            self.ecg_ch_idx = int(ecg_ch_idx)
            self.eda_ch_idx = int(eda_ch_idx)

    def update_config(
        self,
        cfg: PipelineConfig,
        rebuild_ecg_filter: bool = False,
        rebuild_eda_filter: bool = False,
    ) -> None:
        with self._lock:
            self.cfg = cfg
            self._ecg_disp.set_window(self.cfg.display_window_s)
            self._eda_disp.set_window(self.cfg.display_window_s)
            self._eda_tonic_disp.set_window(self.cfg.display_window_s)
            self._rpeak_win.set_window(self.cfg.bpm_window_s)
            self._scr_win.set_window(self.cfg.scr_window_s)
            self._ecg_pre.cfg = self.cfg
            self._eda_pre.cfg = self.cfg
            if rebuild_ecg_filter:
                self._ecg_pre = CausalECGPreprocessor(self._fs, self.cfg)
            if rebuild_eda_filter:
                self._eda_pre = CausalEDAPreprocessor(self._fs, self.cfg)

    def get_filter_errors(self) -> dict[str, dict[str, str]]:
        with self._lock:
            return {
                "ecg": dict(self._ecg_pre.filter_errors),
                "eda": dict(self._eda_pre.filter_errors),
            }

    def start_calibration(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._calibrating = True
            self._cal_start_t = None
            self._cal_progress_pct = 0
            self._cal_ecg_t.clear()
            self._cal_ecg_y.clear()
            self._cal_eda_t.clear()
            self._cal_eda_y.clear()

    def _finish_calibration_locked(self) -> None:
        self._calibrating = False
        self._cal_progress_pct = 100
        parts: list[str] = []
        if self._cal_ecg_t:
            t_e = np.concatenate(self._cal_ecg_t)
            y_e = np.concatenate(self._cal_ecg_y)
            cal_ecg = self._ecg_det.calibrate(t_e, y_e, self._fs)
            parts.append(f"ECG: {cal_ecg.n_beats_used} beats (r>={cal_ecg.corr_threshold:.2f})")
        if self._cal_eda_t:
            t_d = np.concatenate(self._cal_eda_t)
            y_d = np.concatenate(self._cal_eda_y)
            cal_eda = self._eda_det.calibrate(t_d, y_d, self._fs)
            parts.append(f"EDA: {cal_eda.n_events_used} SCRs (r>={cal_eda.corr_threshold:.2f})")
        self._cal_summary = " | ".join(parts) if parts else "Calibration complete"
        self.calibration_version += 1

    def step_once(self) -> bool:
        """Pull queued samples, run causal DSP + detectors, and push LSL streams."""
        with self._lock:
            if not self._running or self._source is None:
                return False
            t_rel, lsl_t, data = self._source.pull()
            if len(t_rel) == 0:
                return False

            self.total_samples_processed += int(len(t_rel))
            self._t_latest = float(t_rel[-1])
            ecg_idx = self.ecg_ch_idx
            eda_idx = self.eda_ch_idx
            rec_sig = self.record_signal_buffers
            rec_feat = self.record_feature_buffers

            rpeak_flag = 0.0
            rpeak_conf = 0.0
            scr_flag = 0.0
            scr_conf = 0.0
            ecg_val = 0.0
            eda_val = 0.0
            tonic_val = 0.0
            phasic_val = 0.0

            n_samples = len(t_rel)
            ecg_filt_arr = np.zeros(n_samples, dtype=float)
            ecg_qrs_arr = np.zeros(n_samples, dtype=float)
            eda_filt_arr = np.zeros(n_samples, dtype=float)
            eda_tonic_arr = np.zeros(n_samples, dtype=float)
            eda_phasic_arr = np.zeros(n_samples, dtype=float)

            if 0 <= ecg_idx < data.shape[1]:
                ecg_raw = data[:, ecg_idx]
                ecg_filt, ecg_qrs = self._ecg_pre.process(ecg_raw)
                ecg_filt_arr = ecg_filt
                ecg_qrs_arr = ecg_qrs
                if rec_sig:
                    self._ecg_disp.append(t_rel, ecg_filt)
                self._ecg_amp_win.append(t_rel, ecg_filt)
                ecg_val = float(ecg_filt[-1])

                if self._calibrating:
                    self._cal_ecg_t.append(t_rel)
                    self._cal_ecg_y.append(ecg_qrs)

                evs, r_scores = self._ecg_det.process_chunk(
                    t_rel, ecg_qrs, ecg_filt, lsl_t, online_adapt=self.cfg.online_adapt
                )
                if len(r_scores) > 0:
                    rpeak_conf = float(np.max(r_scores))
                for ev in evs:
                    rpeak_flag = 1.0
                    rpeak_conf = ev.confidence
                    self._rpeak_win.add(ev.time_s, ev.amplitude)
                    if rec_feat:
                        self._disp_rpeaks.append((ev.time_s, ev.amplitude))
                    self._last_event = ev
                    if self._outlets is not None:
                        self._outlets.push_event(ev)

            if 0 <= eda_idx < data.shape[1]:
                eda_raw = data[:, eda_idx]
                eda_filt, eda_tonic, eda_phasic = self._eda_pre.process(eda_raw)
                eda_filt_arr = eda_filt
                eda_tonic_arr = eda_tonic
                eda_phasic_arr = eda_phasic
                if rec_sig:
                    self._eda_disp.append(t_rel, eda_filt)
                if rec_feat:
                    self._eda_tonic_disp.append(t_rel, eda_tonic)
                eda_val = float(eda_filt[-1])
                tonic_val = float(eda_tonic[-1])
                phasic_val = float(eda_phasic[-1])

                if self._calibrating:
                    self._cal_eda_t.append(t_rel)
                    self._cal_eda_y.append(eda_phasic)

                evs, r_scores = self._eda_det.process_chunk(
                    t_rel, eda_phasic, lsl_t, online_adapt=self.cfg.online_adapt
                )
                if len(r_scores) > 0:
                    scr_conf = float(np.max(r_scores))
                for ev in evs:
                    scr_flag = 1.0
                    scr_conf = ev.confidence
                    self._scr_win.add(ev.time_s, ev.amplitude)
                    if rec_feat:
                        self._disp_scrs.append((ev.time_s, float(eda_filt[-1])))
                    self._last_event = ev
                    if self._outlets is not None:
                        self._outlets.push_event(ev)

            if rec_feat:
                self._trim_display_events_locked()

            if self._calibrating:
                if self._cal_start_t is None:
                    self._cal_start_t = float(t_rel[0])
                elapsed_cal = self._t_latest - self._cal_start_t
                self._cal_progress_pct = int(
                    min(100.0, max(0.0, 100.0 * elapsed_cal / max(1.0, self.cfg.calibration_duration_s)))
                )
                if elapsed_cal >= self.cfg.calibration_duration_s:
                    self._finish_calibration_locked()

            bpm = self._rpeak_win.rolling_bpm(self._t_latest)
            scr_rate = self._scr_win.rolling_rate_per_min(self._t_latest, self._t_latest)
            self._last_snapshot = FeatureSnapshot(
                ecg_filtered=ecg_val,
                bpm_rolling=bpm,
                ecg_rpeak_event=rpeak_flag,
                ecg_rpeak_conf=rpeak_conf,
                eda_filtered=eda_val,
                eda_tonic=tonic_val,
                eda_phasic=phasic_val,
                eda_scr_event=scr_flag,
                eda_scr_conf=scr_conf,
                eda_scr_rate_per_min=scr_rate,
            )
            if self._outlets is not None:
                if self._outlets.raw_outlet is not None:
                    self._outlets.push_raw_chunk(data, lsl_t)
                filtered_mat = np.column_stack(
                    [ecg_filt_arr, ecg_qrs_arr, eda_filt_arr, eda_tonic_arr, eda_phasic_arr]
                )
                self._outlets.push_filtered_chunk(filtered_mat, lsl_t)
                self._outlets.push_features(
                    self._last_snapshot,
                    float(lsl_t[-1]),
                    force=(rpeak_flag > 0.0 or scr_flag > 0.0),
                )
            return True

    def get_graph_render_data(
        self,
        include_signals: bool = True,
        include_features: bool = True,
    ) -> tuple[
        np.ndarray,
        np.ndarray,
        list[tuple[float, float]],
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        list[tuple[float, float]],
        float,
    ]:
        """Thread-safe snapshot of requested signal traces and/or graph feature overlays."""
        empty = np.empty(0, dtype=float)
        with self._lock:
            if include_signals:
                et, ey = self._ecg_disp.get_display_curve()
                dt, dy = self._eda_disp.get_display_curve()
            else:
                et = ey = dt = dy = empty

            if include_features:
                self._trim_display_events_locked()
                tt, ty = self._eda_tonic_disp.get_display_curve()
                rpeaks = list(self._disp_rpeaks)
                scrs = list(self._disp_scrs)
            else:
                tt = ty = empty
                rpeaks = []
                scrs = []

            return et, ey, rpeaks, dt, dy, tt, ty, scrs, self._t_latest

    def get_signal_render_data(
        self,
    ) -> tuple[
        np.ndarray,
        np.ndarray,
        list[tuple[float, float]],
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        list[tuple[float, float]],
        float,
    ]:
        """Backwards-compatible full graph render snapshot."""
        return self.get_graph_render_data(include_signals=True, include_features=True)

    def get_feature_render_data(
        self,
    ) -> tuple[FeatureSnapshot, DetectedEvent | None, float, bool, int, str, int, int]:
        """Thread-safe lightweight snapshot of latest features and calibration progress."""
        with self._lock:
            return (
                self._last_snapshot,
                self._last_event,
                self._t_latest,
                self._calibrating,
                self._cal_progress_pct,
                self._cal_summary,
                self.calibration_version,
                self.total_samples_processed,
            )

    def get_templates(self) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            return (
                np.asarray(self._ecg_det.calibration.template, dtype=float),
                np.asarray(self._eda_det.calibration.template, dtype=float),
            )

    def save_calibration(self, path: Path) -> Path:
        path = Path(path)
        with self._lock:
            payload = {
                "version": 1,
                "config": self.cfg.to_dict(),
                "ecg_calibration": self._ecg_det.calibration.to_dict(),
                "eda_calibration": self._eda_det.calibration.to_dict(),
            }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def load_calibration(self, path: Path) -> PipelineConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        with self._lock:
            if "config" in data:
                self.cfg = PipelineConfig.from_dict(data["config"])
                self.update_config(self.cfg, rebuild_ecg_filter=True, rebuild_eda_filter=True)
            if "ecg_calibration" in data:
                self._ecg_det.apply_calibration(ECGCalibration.from_dict(data["ecg_calibration"]))
            if "eda_calibration" in data:
                self._eda_det.apply_calibration(EDACalibration.from_dict(data["eda_calibration"]))
            self._cal_summary = "Loaded saved calibration templates."
            self.calibration_version += 1
            return self.cfg
