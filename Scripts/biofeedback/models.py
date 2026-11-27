"""Data models, LSL feature schema, and calibration persistence for Real-Time Biofeedback."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

REALTIME_FILTER_TYPES: tuple[str, ...] = ("lowpass", "highpass", "bandpass", "bandstop")

REALTIME_FILTER_DISPLAY_NAMES: dict[str, str] = {
    "lowpass": "Lowpass",
    "highpass": "Highpass",
    "bandpass": "Bandpass",
    "bandstop": "Bandstop",
}


def new_filter_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class CausalFilterSpec:
    """Single causal IIR Butterworth filter stage for ECG or EDA pipelines."""

    filter_type: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    id: str = field(default_factory=new_filter_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filter_type": self.filter_type,
            "enabled": bool(self.enabled),
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CausalFilterSpec":
        return cls(
            id=str(data.get("id") or new_filter_id()),
            filter_type=str(data["filter_type"]),
            enabled=bool(data.get("enabled", True)),
            params=dict(data.get("params", {})),
        )


def default_ecg_filters(low_hz: float = 0.5, high_hz: float = 40.0) -> list[CausalFilterSpec]:
    return [
        CausalFilterSpec(
            filter_type="bandpass",
            params={"low_hz": float(low_hz), "high_hz": float(high_hz), "order": 4},
        )
    ]


def default_eda_filters(lp_hz: float = 1.5) -> list[CausalFilterSpec]:
    return [
        CausalFilterSpec(
            filter_type="lowpass",
            params={"cutoff_hz": float(lp_hz), "order": 3},
        )
    ]


FEATURE_CHANNELS: list[tuple[str, str]] = [
    ("ecg_filtered", "raw"),
    ("bpm_rolling", "BPM"),
    ("ecg_rpeak_event", "flag"),
    ("ecg_rpeak_conf", "corr"),
    ("eda_filtered", "uS"),
    ("eda_tonic", "uS"),
    ("eda_phasic", "uS"),
    ("eda_scr_event", "flag"),
    ("eda_scr_conf", "corr"),
    ("eda_scr_rate_per_min", "1/min"),
]

FILTERED_CHANNELS: list[tuple[str, str]] = [
    ("ecg_filtered", "raw"),
    ("ecg_qrs_band", "raw"),
    ("eda_filtered", "uS"),
    ("eda_tonic", "uS"),
    ("eda_phasic", "uS"),
]


@dataclass
class PipelineConfig:
    """Configuration for causal pre-processing, multi-res display, and rolling stat windows."""

    display_window_s: float = 20.0
    bpm_window_s: float = 10.0
    scr_window_s: float = 30.0
    amp_window_s: float = 2.0

    ecg_notch_50hz: bool = True
    ecg_bp_low_hz: float = 0.5
    ecg_bp_high_hz: float = 40.0
    ecg_qrs_low_hz: float = 5.0
    ecg_qrs_high_hz: float = 18.0
    ecg_qrs_order: int = 3

    eda_lp_hz: float = 1.5
    eda_tonic_lp_hz: float = 0.05
    eda_tonic_order: int = 2
    eda_scale_m: float = 1.0
    eda_offset_b: float = 0.0
    eda_invert: bool = False
    eda_invert_num: float = 1e6
    eda_unit: str = "μS"

    calibration_duration_s: float = 15.0
    online_adapt: bool = False

    ecg_filters: list[CausalFilterSpec] = field(default_factory=lambda: default_ecg_filters(0.5, 40.0))
    eda_filters: list[CausalFilterSpec] = field(default_factory=lambda: default_eda_filters(1.5))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ecg_filters"] = [f.to_dict() for f in self.ecg_filters]
        d["eda_filters"] = [f.to_dict() for f in self.eda_filters]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineConfig":
        valid = {
            k: v
            for k, v in data.items()
            if k in cls.__dataclass_fields__ and k not in {"ecg_filters", "eda_filters"}
        }
        cfg = cls(**valid)
        if "ecg_filters" in data and isinstance(data["ecg_filters"], list):
            cfg.ecg_filters = [CausalFilterSpec.from_dict(item) for item in data["ecg_filters"]]
        else:
            cfg.ecg_filters = default_ecg_filters(cfg.ecg_bp_low_hz, cfg.ecg_bp_high_hz)
        if "eda_filters" in data and isinstance(data["eda_filters"], list):
            cfg.eda_filters = [CausalFilterSpec.from_dict(item) for item in data["eda_filters"]]
        else:
            cfg.eda_filters = default_eda_filters(cfg.eda_lp_hz)
        return cfg


@dataclass
class ECGCalibration:
    """Learned template and thresholds for causal QRS R-peak detection."""

    fs: float = 250.0
    template: list[float] = field(default_factory=list)
    corr_threshold: float = 0.72
    amp_threshold: float = 0.15
    polarity: int = 1
    refractory_s: float = 0.30
    n_beats_used: int = 0
    noise_mad: float = 0.0
    calibrated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ECGCalibration":
        return cls(
            fs=float(data.get("fs", 250.0)),
            template=[float(x) for x in data.get("template", [])],
            corr_threshold=float(data.get("corr_threshold", 0.72)),
            amp_threshold=float(data.get("amp_threshold", 0.15)),
            polarity=int(data.get("polarity", 1)),
            refractory_s=float(data.get("refractory_s", 0.30)),
            n_beats_used=int(data.get("n_beats_used", 0)),
            noise_mad=float(data.get("noise_mad", 0.0)),
            calibrated=bool(data.get("calibrated", False)),
        )


@dataclass
class EDACalibration:
    """Learned template and thresholds for causal EDA SCR event detection."""

    fs_internal: float = 10.0
    template: list[float] = field(default_factory=list)
    corr_threshold: float = 0.60
    amp_threshold: float = 0.05
    refractory_s: float = 2.0
    n_events_used: int = 0
    noise_mad: float = 0.0
    calibrated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EDACalibration":
        return cls(
            fs_internal=float(data.get("fs_internal", 10.0)),
            template=[float(x) for x in data.get("template", [])],
            corr_threshold=float(data.get("corr_threshold", 0.60)),
            amp_threshold=float(data.get("amp_threshold", 0.05)),
            refractory_s=float(data.get("refractory_s", 2.0)),
            n_events_used=int(data.get("n_events_used", 0)),
            noise_mad=float(data.get("noise_mad", 0.0)),
            calibrated=bool(data.get("calibrated", False)),
        )


@dataclass
class DetectedEvent:
    """Single detected physiological event (R-peak or SCR)."""

    kind: str  # "R_PEAK" or "SCR"
    time_s: float
    lsl_time: float
    amplitude: float
    confidence: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeatureSnapshot:
    """Latest processed feature vector aligned with FEATURE_CHANNELS."""

    ecg_filtered: float = 0.0
    bpm_rolling: float = 0.0
    ecg_rpeak_event: float = 0.0
    ecg_rpeak_conf: float = 0.0
    eda_filtered: float = 0.0
    eda_tonic: float = 0.0
    eda_phasic: float = 0.0
    eda_scr_event: float = 0.0
    eda_scr_conf: float = 0.0
    eda_scr_rate_per_min: float = 0.0

    def to_list(self) -> list[float]:
        return [
            float(self.ecg_filtered),
            float(self.bpm_rolling),
            float(self.ecg_rpeak_event),
            float(self.ecg_rpeak_conf),
            float(self.eda_filtered),
            float(self.eda_tonic),
            float(self.eda_phasic),
            float(self.eda_scr_event),
            float(self.eda_scr_conf),
            float(self.eda_scr_rate_per_min),
        ]
