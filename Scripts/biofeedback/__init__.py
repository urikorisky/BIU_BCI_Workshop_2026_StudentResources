"""Real-Time Biofeedback Studio package."""

from .detectors import ECGRPeakTemplateDetector, EDASCRTemplateDetector, make_qrs_prior, make_scr_prior
from .dsp_realtime import (
    CausalECGPreprocessor,
    CausalEDAPreprocessor,
    CausalSOSFilter,
    MultiResRingBuffer,
    RollingAmplitudeWindow,
    RollingEventWindow,
)
from .engine import BiofeedbackEngine
from .lsl_io import BiofeedbackLSLOutlets, LSLInputSource, SyntheticBioSource, discover_lsl_streams
from .models import (
    FEATURE_CHANNELS,
    FILTERED_CHANNELS,
    REALTIME_FILTER_DISPLAY_NAMES,
    REALTIME_FILTER_TYPES,
    CausalFilterSpec,
    DetectedEvent,
    ECGCalibration,
    EDACalibration,
    FeatureSnapshot,
    PipelineConfig,
)
from .recorder import LSLSessionRecorder

__all__ = [
    "BiofeedbackEngine",
    "BiofeedbackLSLOutlets",
    "CausalECGPreprocessor",
    "CausalEDAPreprocessor",
    "CausalFilterSpec",
    "CausalSOSFilter",
    "DetectedEvent",
    "ECGCalibration",
    "ECGRPeakTemplateDetector",
    "EDACalibration",
    "EDASCRTemplateDetector",
    "FEATURE_CHANNELS",
    "FILTERED_CHANNELS",
    "FeatureSnapshot",
    "LSLInputSource",
    "LSLSessionRecorder",
    "MultiResRingBuffer",
    "PipelineConfig",
    "REALTIME_FILTER_DISPLAY_NAMES",
    "REALTIME_FILTER_TYPES",
    "RollingAmplitudeWindow",
    "RollingEventWindow",
    "SyntheticBioSource",
    "discover_lsl_streams",
    "make_qrs_prior",
    "make_scr_prior",
]
