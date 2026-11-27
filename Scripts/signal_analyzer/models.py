"""Data models and analysis-state serialization for Signal Analyzer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_PALETTE: list[str] = [
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#17becf",  # cyan
    "#bcbd22",  # olive
    "#393b79",  # indigo
    "#637939",  # sage
]

FILTER_TYPES: tuple[str, ...] = ("lowpass", "highpass", "bandpass", "bandstop", "step", "knee")

FILTER_DISPLAY_NAMES: dict[str, str] = {
    "lowpass": "Lowpass",
    "highpass": "Highpass",
    "bandpass": "Bandpass",
    "bandstop": "Bandstop",
    "step": "Step",
    "knee": "Knee",
}


def new_filter_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class FilterSpec:
    """Configuration for a single filter stage in a channel's filter stack."""

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
    def from_dict(cls, data: dict[str, Any]) -> "FilterSpec":
        return cls(
            id=str(data.get("id") or new_filter_id()),
            filter_type=str(data["filter_type"]),
            enabled=bool(data.get("enabled", True)),
            params=dict(data.get("params", {})),
        )


@dataclass
class DetectorConfig:
    """Configuration for an event detector attached to a channel."""

    detector_type: str
    enabled: bool = False
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector_type": self.detector_type,
            "enabled": bool(self.enabled),
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DetectorConfig":
        return cls(
            detector_type=str(data["detector_type"]),
            enabled=bool(data.get("enabled", False)),
            params=dict(data.get("params", {})),
        )


@dataclass
class ChannelState:
    """Per-channel display, filtering, and event detection configuration."""

    index: int
    original_name: str
    display_name: str
    unit: str = ""
    visible: bool = True
    color: str = "#1f77b4"
    line_width: float = 1.4
    custom_y_scale: bool = False
    raw_y_min: float | None = None
    raw_y_max: float | None = None
    filt_y_min: float | None = None
    filt_y_max: float | None = None
    filters: list[FilterSpec] = field(default_factory=list)
    detectors: dict[str, DetectorConfig] = field(default_factory=dict)
    derived_spec: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "original_name": self.original_name,
            "display_name": self.display_name,
            "unit": self.unit,
            "visible": bool(self.visible),
            "color": self.color,
            "line_width": float(self.line_width),
            "custom_y_scale": bool(self.custom_y_scale),
            "raw_y_min": None if self.raw_y_min is None else float(self.raw_y_min),
            "raw_y_max": None if self.raw_y_max is None else float(self.raw_y_max),
            "filt_y_min": None if self.filt_y_min is None else float(self.filt_y_min),
            "filt_y_max": None if self.filt_y_max is None else float(self.filt_y_max),
            "filters": [f.to_dict() for f in self.filters],
            "detectors": {k: v.to_dict() for k, v in self.detectors.items()},
            "derived_spec": dict(self.derived_spec) if self.derived_spec else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChannelState":
        detectors = {
            k: DetectorConfig.from_dict(v)
            for k, v in data.get("detectors", {}).items()
        }
        legacy_min = data.get("y_min")
        legacy_max = data.get("y_max")
        raw_min = data.get("raw_y_min", legacy_min)
        raw_max = data.get("raw_y_max", legacy_max)
        filt_min = data.get("filt_y_min", legacy_min)
        filt_max = data.get("filt_y_max", legacy_max)
        return cls(
            index=int(data["index"]),
            original_name=str(data.get("original_name", f"Ch{int(data['index']) + 1}")),
            display_name=str(data.get("display_name", f"Ch{int(data['index']) + 1}")),
            unit=str(data.get("unit", "")),
            visible=bool(data.get("visible", True)),
            color=str(data.get("color", DEFAULT_PALETTE[int(data["index"]) % len(DEFAULT_PALETTE)])),
            line_width=float(data.get("line_width", 1.4)),
            custom_y_scale=bool(data.get("custom_y_scale", False)),
            raw_y_min=None if raw_min is None else float(raw_min),
            raw_y_max=None if raw_max is None else float(raw_max),
            filt_y_min=None if filt_min is None else float(filt_min),
            filt_y_max=None if filt_max is None else float(filt_max),
            filters=[FilterSpec.from_dict(f) for f in data.get("filters", [])],
            detectors=detectors,
            derived_spec=dict(data["derived_spec"]) if data.get("derived_spec") else None,
        )


@dataclass
class Recording:
    """Loaded multi-channel time-series recording."""

    source_path: Path | None
    source_format: str
    stream_name: str
    stream_index: int | None
    time_s: np.ndarray
    lsl_timestamps: np.ndarray | None
    sample_rate: float
    raw_data: np.ndarray  # shape (n_samples, n_channels)
    channel_names: list[str]
    markers: list[tuple[float, str]] = field(default_factory=list)

    @property
    def n_samples(self) -> int:
        return int(self.raw_data.shape[0])

    @property
    def n_channels(self) -> int:
        return int(self.raw_data.shape[1])

    @property
    def duration_s(self) -> float:
        if len(self.time_s) < 2:
            return 0.0
        return float(self.time_s[-1] - self.time_s[0])


@dataclass
class EventDetectionResult:
    """Detected events for a single (channel, detector_type) pair."""

    detector_type: str
    event_label: str
    channel_index: int
    channel_name: str
    indices: np.ndarray
    times: np.ndarray
    processed_values: np.ndarray
    raw_values: np.ndarray
    marker_symbol: str
    marker_color: str


@dataclass
class AnalysisState:
    """Serializable analysis state for saving and loading sessions."""

    version: int
    source_path: str | None
    source_format: str
    stream_index: int | None
    stream_name: str
    ui_mode: str
    view_state: dict[str, Any]
    channels: list[ChannelState]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "source_path": self.source_path,
            "source_format": self.source_format,
            "stream_index": self.stream_index,
            "stream_name": self.stream_name,
            "ui_mode": self.ui_mode,
            "view_state": dict(self.view_state),
            "channels": [ch.to_dict() for ch in self.channels],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisState":
        return cls(
            version=int(data.get("version", 1)),
            source_path=data.get("source_path"),
            source_format=str(data.get("source_format", "csv")),
            stream_index=data.get("stream_index"),
            stream_name=str(data.get("stream_name", "")),
            ui_mode=str(data.get("ui_mode", "basic")),
            view_state=dict(data.get("view_state", {})),
            channels=[ChannelState.from_dict(c) for c in data.get("channels", [])],
        )


def build_default_channel_states(
    recording: Recording,
    default_visible_limit: int = 16,
    initial_visible_count: int = 8,
) -> list[ChannelState]:
    """Create initial ChannelState objects for all channels in a Recording."""
    many = recording.n_channels > default_visible_limit
    states: list[ChannelState] = []
    for idx, name in enumerate(recording.channel_names):
        states.append(
            ChannelState(
                index=idx,
                original_name=name,
                display_name=name,
                visible=(idx < initial_visible_count) if many else True,
                color=DEFAULT_PALETTE[idx % len(DEFAULT_PALETTE)],
                line_width=1.4,
            )
        )
    return states
