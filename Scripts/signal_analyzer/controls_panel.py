"""Right-side Signal Manipulation Panel with Basic/Advanced mode separation."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .dsp import default_filter_params, format_linear_combination_formula
from .events import DETECTOR_REGISTRY
from .models import (
    FILTER_DISPLAY_NAMES,
    FILTER_TYPES,
    ChannelState,
    DetectorConfig,
    FilterSpec,
)


def _swatch_icon(hex_color: str) -> QIcon:
    pix = QPixmap(12, 12)
    pix.fill(QColor(hex_color))
    return QIcon(pix)


class FilterCard(QFrame):
    """Collapsible/ordered card representing one filter stage in a channel's stack."""

    changed = pyqtSignal()
    move_up = pyqtSignal(str)
    move_down = pyqtSignal(str)
    removed = pyqtSignal(str)

    def __init__(
        self,
        spec: FilterSpec,
        index: int,
        total: int,
        fs: float,
        advanced_mode: bool,
        error_msg: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.spec = spec
        self._fs = fs
        self._advanced = advanced_mode

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "FilterCard { background: #f9fafb; border: 1px solid #d1d5db; border-radius: 6px; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # Header row: enable checkbox, stage label, move up/down, delete
        header = QHBoxLayout()
        header.setSpacing(4)
        self.enable_cb = QCheckBox(f"{index + 1}. {FILTER_DISPLAY_NAMES.get(spec.filter_type, spec.filter_type)}")
        self.enable_cb.setChecked(spec.enabled)
        self.enable_cb.setStyleSheet("font-weight: 600; color: #111827;")
        self.enable_cb.toggled.connect(self._on_enable_toggled)
        header.addWidget(self.enable_cb, 1)

        btn_up = QPushButton("↑")
        btn_up.setFixedSize(24, 22)
        btn_up.setEnabled(index > 0)
        btn_up.setToolTip("Move filter earlier in stack")
        btn_up.clicked.connect(lambda: self.move_up.emit(self.spec.id))

        btn_dn = QPushButton("↓")
        btn_dn.setFixedSize(24, 22)
        btn_dn.setEnabled(index < total - 1)
        btn_dn.setToolTip("Move filter later in stack")
        btn_dn.clicked.connect(lambda: self.move_down.emit(self.spec.id))

        btn_del = QPushButton("×")
        btn_del.setFixedSize(24, 22)
        btn_del.setToolTip("Remove filter")
        btn_del.setStyleSheet("color: #b91c1c; font-weight: bold;")
        btn_del.clicked.connect(lambda: self.removed.emit(self.spec.id))

        header.addWidget(btn_up)
        header.addWidget(btn_dn)
        header.addWidget(btn_del)
        root.addLayout(header)

        # Parameter grid
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        max_freq = max(1.0, fs * 0.499)

        row = 0
        ftype = spec.filter_type

        if ftype in {"lowpass", "highpass"}:
            grid.addWidget(QLabel("Cutoff (Hz):"), row, 0)
            self.cutoff_spin = self._freq_spin(spec.params.get("cutoff_hz", 10.0), 0.01, max_freq)
            self.cutoff_spin.valueChanged.connect(lambda v: self._set_param("cutoff_hz", v))
            grid.addWidget(self.cutoff_spin, row, 1)
            row += 1

        elif ftype in {"bandpass", "bandstop"}:
            grid.addWidget(QLabel("Low (Hz):"), row, 0)
            self.low_spin = self._freq_spin(spec.params.get("low_hz", 5.0), 0.01, max_freq)
            self.low_spin.valueChanged.connect(lambda v: self._set_param("low_hz", v))
            grid.addWidget(self.low_spin, row, 1)
            row += 1

            grid.addWidget(QLabel("High (Hz):"), row, 0)
            self.high_spin = self._freq_spin(spec.params.get("high_hz", 35.0), 0.01, max_freq)
            self.high_spin.valueChanged.connect(lambda v: self._set_param("high_hz", v))
            grid.addWidget(self.high_spin, row, 1)
            row += 1

        elif ftype in {"step", "knee"}:
            mode = str(spec.params.get("mode", "lowpass"))
            if advanced_mode:
                grid.addWidget(QLabel("Pass Mode:"), row, 0)
                self.mode_combo = QComboBox()
                self.mode_combo.addItems(["lowpass", "highpass", "bandpass", "bandstop"])
                self.mode_combo.setCurrentText(mode)
                self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
                grid.addWidget(self.mode_combo, row, 1)
                row += 1

            if mode in {"lowpass", "highpass"}:
                grid.addWidget(QLabel("Cutoff (Hz):"), row, 0)
                self.cutoff_spin = self._freq_spin(spec.params.get("cutoff_hz", 40.0), 0.0, max_freq)
                self.cutoff_spin.valueChanged.connect(lambda v: self._set_param("cutoff_hz", v))
                grid.addWidget(self.cutoff_spin, row, 1)
                row += 1
            else:
                grid.addWidget(QLabel("Low (Hz):"), row, 0)
                self.low_spin = self._freq_spin(spec.params.get("low_hz", 5.0), 0.0, max_freq)
                self.low_spin.valueChanged.connect(lambda v: self._set_param("low_hz", v))
                grid.addWidget(self.low_spin, row, 1)
                row += 1

                grid.addWidget(QLabel("High (Hz):"), row, 0)
                self.high_spin = self._freq_spin(spec.params.get("high_hz", 35.0), 0.0, max_freq)
                self.high_spin.valueChanged.connect(lambda v: self._set_param("high_hz", v))
                grid.addWidget(self.high_spin, row, 1)
                row += 1

            if ftype == "knee":
                grid.addWidget(QLabel("Knee Width (Hz):"), row, 0)
                self.trans_spin = self._freq_spin(spec.params.get("transition_hz", 4.0), 0.05, max_freq)
                self.trans_spin.valueChanged.connect(lambda v: self._set_param("transition_hz", v))
                grid.addWidget(self.trans_spin, row, 1)
                row += 1

        # Advanced-only filter tuning parameters
        if advanced_mode and ftype in {"lowpass", "highpass", "bandpass", "bandstop"}:
            grid.addWidget(QLabel("Order:"), row, 0)
            order_spin = QSpinBox()
            order_spin.setRange(1, 10)
            order_spin.setValue(int(spec.params.get("order", 4)))
            order_spin.valueChanged.connect(lambda v: self._set_param("order", v))
            grid.addWidget(order_spin, row, 1)
            row += 1

            zp_cb = QCheckBox("Zero-phase (filtfilt)")
            zp_cb.setChecked(bool(spec.params.get("zero_phase", True)))
            zp_cb.toggled.connect(lambda v: self._set_param("zero_phase", v))
            grid.addWidget(zp_cb, row, 0, 1, 2)
            row += 1

        if advanced_mode and ftype == "knee":
            grid.addWidget(QLabel("Knee Shape:"), row, 0)
            shape_combo = QComboBox()
            shape_combo.addItems(["raised_cosine", "linear", "gaussian"])
            shape_combo.setCurrentText(str(spec.params.get("shape", "raised_cosine")))
            shape_combo.currentTextChanged.connect(lambda v: self._set_param("shape", v))
            grid.addWidget(shape_combo, row, 1)
            row += 1

        root.addLayout(grid)

        if error_msg:
            err_lbl = QLabel(error_msg)
            err_lbl.setWordWrap(True)
            err_lbl.setStyleSheet("color: #b91c1c; font-size: 11px;")
            root.addWidget(err_lbl)

    @staticmethod
    def _freq_spin(val: float, lo: float, hi: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(lo, max(lo + 0.1, hi))
        spin.setSingleStep(0.5)
        spin.setValue(float(val))
        return spin

    def _on_enable_toggled(self, checked: bool) -> None:
        self.spec.enabled = checked
        self.changed.emit()

    def _on_mode_changed(self, mode: str) -> None:
        self.spec.params["mode"] = mode
        self.changed.emit()

    def _set_param(self, key: str, value: Any) -> None:
        self.spec.params[key] = value
        self.changed.emit()


class ControlsPanel(QWidget):
    """Right-side panel for per-channel display, stacked filters, and event detection."""

    channel_selected = pyqtSignal(int)
    channel_display_changed = pyqtSignal()
    channel_filters_changed = pyqtSignal(int)
    channel_detectors_changed = pyqtSignal(int)
    autoscale_channel_requested = pyqtSignal(int)
    revert_scale_requested = pyqtSignal(int)
    show_all_requested = pyqtSignal(bool)
    y_lock_changed = pyqtSignal(bool)
    spectrum_options_changed = pyqtSignal(str, bool)
    axis_fonts_changed = pyqtSignal(int, int)
    create_derived_requested = pyqtSignal()
    edit_derived_requested = pyqtSignal(int)
    delete_derived_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._channels: list[ChannelState] = []
        self._selected_idx: int = 0
        self._fs: float = 250.0
        self._advanced_mode: bool = False
        self._filter_errors: dict[int, dict[str, str]] = {}
        self._updating_ui: bool = False

        self.setMinimumWidth(320)
        self.setMaximumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(10, 10, 10, 10)
        self._body_layout.setSpacing(10)
        scroll.setWidget(body)

        self._build_channel_selector()
        self._build_display_group()
        self._build_filters_group()
        self._build_events_group()
        self._build_axes_font_group()
        self._build_advanced_plot_group()
        self._body_layout.addStretch(1)

        self._set_enabled_all(False)
        self._apply_mode_visibility()

    def _build_channel_selector(self) -> None:
        box = QGroupBox("Channel")
        lay = QVBoxLayout(box)
        lay.setSpacing(6)

        self.ch_combo = QComboBox()
        self.ch_combo.currentIndexChanged.connect(self._on_channel_combo_changed)
        lay.addWidget(self.ch_combo)

        self.bulk_vis_row = QWidget()
        bv_lay = QHBoxLayout(self.bulk_vis_row)
        bv_lay.setContentsMargins(0, 0, 0, 0)
        btn_show_all = QPushButton("Show All")
        btn_hide_all = QPushButton("Hide All")
        btn_show_all.clicked.connect(lambda: self.show_all_requested.emit(True))
        btn_hide_all.clicked.connect(lambda: self.show_all_requested.emit(False))
        bv_lay.addWidget(btn_show_all)
        bv_lay.addWidget(btn_hide_all)
        lay.addWidget(self.bulk_vis_row)

        self.derived_row = QWidget()
        dr_lay = QHBoxLayout(self.derived_row)
        dr_lay.setContentsMargins(0, 0, 0, 0)
        self.btn_add_derived = QPushButton("+ Derived Ch...")
        self.btn_add_derived.setToolTip("Create a computed channel (linear combination + units)")
        self.btn_add_derived.clicked.connect(self.create_derived_requested.emit)
        self.btn_edit_derived = QPushButton("Edit")
        self.btn_edit_derived.setToolTip("Edit selected derived channel formula & units")
        self.btn_edit_derived.clicked.connect(self._on_edit_derived_clicked)
        self.btn_del_derived = QPushButton("Delete")
        self.btn_del_derived.setToolTip("Delete selected derived channel")
        self.btn_del_derived.setStyleSheet("color: #b91c1c;")
        self.btn_del_derived.clicked.connect(self._on_delete_derived_clicked)
        dr_lay.addWidget(self.btn_add_derived, 2)
        dr_lay.addWidget(self.btn_edit_derived, 1)
        dr_lay.addWidget(self.btn_del_derived, 1)
        lay.addWidget(self.derived_row)

        self._body_layout.addWidget(box)

    def _build_display_group(self) -> None:
        self.display_group = QGroupBox("Channel Display & Y-Scale")
        lay = QVBoxLayout(self.display_group)
        lay.setSpacing(8)

        # Rename + Show/Hide
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(self._on_name_edited)
        name_row.addWidget(self.name_edit, 1)
        lay.addLayout(name_row)

        self.unit_row = QWidget()
        unit_lay = QHBoxLayout(self.unit_row)
        unit_lay.setContentsMargins(0, 0, 0, 0)
        unit_lay.addWidget(QLabel("Units:"))
        self.unit_edit = QLineEdit()
        self.unit_edit.setPlaceholderText("e.g. μS, mV")
        self.unit_edit.textEdited.connect(self._on_unit_edited)
        unit_lay.addWidget(self.unit_edit, 1)
        lay.addWidget(self.unit_row)

        self.derived_formula_lbl = QLabel()
        self.derived_formula_lbl.setWordWrap(True)
        self.derived_formula_lbl.setStyleSheet("color: #4b5563; font-size: 11px; font-family: Consolas, monospace;")
        self.derived_formula_lbl.setVisible(False)
        lay.addWidget(self.derived_formula_lbl)

        self.visible_cb = QCheckBox("Show channel in plots")
        self.visible_cb.toggled.connect(self._on_visible_toggled)
        lay.addWidget(self.visible_cb)

        # Advanced-only appearance controls (color & line width)
        self.appearance_row = QWidget()
        app_lay = QHBoxLayout(self.appearance_row)
        app_lay.setContentsMargins(0, 0, 0, 0)
        app_lay.addWidget(QLabel("Color:"))
        self.color_btn = QPushButton()
        self.color_btn.setFixedWidth(48)
        self.color_btn.clicked.connect(self._on_pick_color)
        app_lay.addWidget(self.color_btn)

        app_lay.addWidget(QLabel("Width:"))
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.5, 5.0)
        self.width_spin.setSingleStep(0.2)
        self.width_spin.setDecimals(1)
        self.width_spin.valueChanged.connect(self._on_width_changed)
        app_lay.addWidget(self.width_spin)
        app_lay.addStretch(1)
        lay.addWidget(self.appearance_row)

        # Y-Scale 3-step controls
        self.scale_status_lbl = QLabel("Y-Scale: Shared (Original)")
        self.scale_status_lbl.setStyleSheet("color: #4b5563; font-size: 11px;")
        lay.addWidget(self.scale_status_lbl)

        self.autoscale_btn = QPushButton("Autoscale (Own Y-Axis)")
        self.autoscale_btn.clicked.connect(self._on_autoscale_clicked)
        lay.addWidget(self.autoscale_btn)

        self.custom_scale_box = QWidget()
        cs_lay = QGridLayout(self.custom_scale_box)
        cs_lay.setContentsMargins(0, 0, 0, 0)
        cs_lay.setHorizontalSpacing(6)
        cs_lay.setVerticalSpacing(6)

        cs_lay.addWidget(QLabel("Raw Y Min:"), 0, 0)
        self.raw_ymin_spin = QDoubleSpinBox()
        self.raw_ymin_spin.setRange(-1e9, 1e9)
        self.raw_ymin_spin.setDecimals(3)
        self.raw_ymin_spin.setKeyboardTracking(False)
        self.raw_ymin_spin.valueChanged.connect(self._on_custom_ylim_changed)
        cs_lay.addWidget(self.raw_ymin_spin, 0, 1)

        cs_lay.addWidget(QLabel("Raw Y Max:"), 1, 0)
        self.raw_ymax_spin = QDoubleSpinBox()
        self.raw_ymax_spin.setRange(-1e9, 1e9)
        self.raw_ymax_spin.setDecimals(3)
        self.raw_ymax_spin.setKeyboardTracking(False)
        self.raw_ymax_spin.valueChanged.connect(self._on_custom_ylim_changed)
        cs_lay.addWidget(self.raw_ymax_spin, 1, 1)

        cs_lay.addWidget(QLabel("Filtered Y Min:"), 2, 0)
        self.filt_ymin_spin = QDoubleSpinBox()
        self.filt_ymin_spin.setRange(-1e9, 1e9)
        self.filt_ymin_spin.setDecimals(3)
        self.filt_ymin_spin.setKeyboardTracking(False)
        self.filt_ymin_spin.valueChanged.connect(self._on_custom_ylim_changed)
        cs_lay.addWidget(self.filt_ymin_spin, 2, 1)

        cs_lay.addWidget(QLabel("Filtered Y Max:"), 3, 0)
        self.filt_ymax_spin = QDoubleSpinBox()
        self.filt_ymax_spin.setRange(-1e9, 1e9)
        self.filt_ymax_spin.setDecimals(3)
        self.filt_ymax_spin.setKeyboardTracking(False)
        self.filt_ymax_spin.valueChanged.connect(self._on_custom_ylim_changed)
        cs_lay.addWidget(self.filt_ymax_spin, 3, 1)

        self.reauto_btn = QPushButton("Re-Autoscale")
        self.reauto_btn.clicked.connect(self._on_autoscale_clicked)
        self.revert_btn = QPushButton("Revert to Original Scale")
        self.revert_btn.clicked.connect(self._on_revert_clicked)
        cs_lay.addWidget(self.reauto_btn, 4, 0)
        cs_lay.addWidget(self.revert_btn, 4, 1)

        lay.addWidget(self.custom_scale_box)
        self._body_layout.addWidget(self.display_group)

    def _build_filters_group(self) -> None:
        self.filters_group = QGroupBox("Filter Stack (Sequential)")
        lay = QVBoxLayout(self.filters_group)
        lay.setSpacing(6)

        hint = QLabel("Each filter processes the output of the filter above it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        lay.addWidget(hint)

        self.filter_cards_container = QVBoxLayout()
        self.filter_cards_container.setSpacing(6)
        lay.addLayout(self.filter_cards_container)

        add_row = QHBoxLayout()
        self.add_filter_combo = QComboBox()
        for ftype in FILTER_TYPES:
            self.add_filter_combo.addItem(FILTER_DISPLAY_NAMES[ftype], ftype)
        self.add_filter_btn = QPushButton("+ Add Filter")
        self.add_filter_btn.clicked.connect(self._on_add_filter)
        add_row.addWidget(self.add_filter_combo, 1)
        add_row.addWidget(self.add_filter_btn)
        lay.addLayout(add_row)

        self._body_layout.addWidget(self.filters_group)

    def _build_events_group(self) -> None:
        self.events_group = QGroupBox("Event Detection")
        lay = QVBoxLayout(self.events_group)
        lay.setSpacing(6)

        qrs_spec = DETECTOR_REGISTRY["qrs"]
        qrs_row = QHBoxLayout()
        self.qrs_cb = QCheckBox(qrs_spec.display_name)
        self.qrs_cb.toggled.connect(self._on_qrs_toggled)
        swatch = QLabel("▲")
        swatch.setStyleSheet(f"color: {qrs_spec.marker_color}; font-size: 14px;")
        qrs_row.addWidget(self.qrs_cb, 1)
        qrs_row.addWidget(swatch)
        lay.addLayout(qrs_row)

        # Advanced-only QRS parameters
        self.qrs_adv_box = QWidget()
        q_lay = QGridLayout(self.qrs_adv_box)
        q_lay.setContentsMargins(12, 4, 0, 0)
        q_lay.setHorizontalSpacing(8)
        q_lay.setVerticalSpacing(4)

        self.qrs_bp_cb = QCheckBox("Internal 5–15 Hz pre-filter")
        self.qrs_bp_cb.toggled.connect(lambda v: self._set_qrs_param("use_internal_bandpass", v))
        q_lay.addWidget(self.qrs_bp_cb, 0, 0, 1, 2)

        q_lay.addWidget(QLabel("BP Low (Hz):"), 1, 0)
        self.qrs_bp_lo = QDoubleSpinBox()
        self.qrs_bp_lo.setRange(0.5, 40.0)
        self.qrs_bp_lo.setDecimals(1)
        self.qrs_bp_lo.valueChanged.connect(lambda v: self._set_qrs_param("bp_low_hz", v))
        q_lay.addWidget(self.qrs_bp_lo, 1, 1)

        q_lay.addWidget(QLabel("BP High (Hz):"), 2, 0)
        self.qrs_bp_hi = QDoubleSpinBox()
        self.qrs_bp_hi.setRange(2.0, 100.0)
        self.qrs_bp_hi.setDecimals(1)
        self.qrs_bp_hi.valueChanged.connect(lambda v: self._set_qrs_param("bp_high_hz", v))
        q_lay.addWidget(self.qrs_bp_hi, 2, 1)

        q_lay.addWidget(QLabel("Refractory (ms):"), 3, 0)
        self.qrs_refrac = QDoubleSpinBox()
        self.qrs_refrac.setRange(80.0, 1500.0)
        self.qrs_refrac.setSingleStep(10.0)
        self.qrs_refrac.setDecimals(0)
        self.qrs_refrac.valueChanged.connect(lambda v: self._set_qrs_param("refractory_ms", v))
        q_lay.addWidget(self.qrs_refrac, 3, 1)

        q_lay.addWidget(QLabel("Threshold Factor:"), 4, 0)
        self.qrs_thresh = QDoubleSpinBox()
        self.qrs_thresh.setRange(0.05, 0.95)
        self.qrs_thresh.setSingleStep(0.05)
        self.qrs_thresh.setDecimals(2)
        self.qrs_thresh.valueChanged.connect(lambda v: self._set_qrs_param("threshold_factor", v))
        q_lay.addWidget(self.qrs_thresh, 4, 1)

        q_lay.addWidget(QLabel("Search Window (ms):"), 5, 0)
        self.qrs_search = QDoubleSpinBox()
        self.qrs_search.setRange(10.0, 250.0)
        self.qrs_search.setSingleStep(5.0)
        self.qrs_search.setDecimals(0)
        self.qrs_search.valueChanged.connect(lambda v: self._set_qrs_param("search_window_ms", v))
        q_lay.addWidget(self.qrs_search, 5, 1)

        q_lay.addWidget(QLabel("Polarity:"), 6, 0)
        self.qrs_polarity = QComboBox()
        self.qrs_polarity.addItems(["auto", "positive", "negative"])
        self.qrs_polarity.currentTextChanged.connect(lambda v: self._set_qrs_param("polarity", v))
        q_lay.addWidget(self.qrs_polarity, 6, 1)

        lay.addWidget(self.qrs_adv_box)
        self._body_layout.addWidget(self.events_group)

    def _build_axes_font_group(self) -> None:
        self.axes_font_group = QGroupBox("Axes Font Size")
        lay = QHBoxLayout(self.axes_font_group)
        lay.setSpacing(8)

        lay.addWidget(QLabel("Ticks:"))
        self.tick_font_spin = QSpinBox()
        self.tick_font_spin.setRange(6, 28)
        self.tick_font_spin.setSuffix(" pt")
        self.tick_font_spin.setValue(9)
        self.tick_font_spin.valueChanged.connect(self._on_axis_fonts_changed)
        lay.addWidget(self.tick_font_spin)

        lay.addWidget(QLabel("Labels:"))
        self.label_font_spin = QSpinBox()
        self.label_font_spin.setRange(6, 32)
        self.label_font_spin.setSuffix(" pt")
        self.label_font_spin.setValue(10)
        self.label_font_spin.valueChanged.connect(self._on_axis_fonts_changed)
        lay.addWidget(self.label_font_spin)

        self._body_layout.addWidget(self.axes_font_group)

    def _on_axis_fonts_changed(self) -> None:
        if self._updating_ui:
            return
        self.axis_fonts_changed.emit(
            self.tick_font_spin.value(),
            self.label_font_spin.value(),
        )

    def set_axis_font_sizes(self, tick_pt: int, label_pt: int) -> None:
        self._updating_ui = True
        self.tick_font_spin.setValue(int(tick_pt))
        self.label_font_spin.setValue(int(label_pt))
        self._updating_ui = False

    def _build_advanced_plot_group(self) -> None:
        self.adv_plot_group = QGroupBox("Display & Spectrum Options (Advanced)")
        lay = QVBoxLayout(self.adv_plot_group)
        lay.setSpacing(6)

        self.lock_y_cb = QCheckBox("Lock Spectrum Raw & Filtered Y-Scales")
        self.lock_y_cb.setChecked(True)
        self.lock_y_cb.toggled.connect(self.y_lock_changed.emit)
        lay.addWidget(self.lock_y_cb)

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        grid.addWidget(QLabel("Spectrum Method:"), 0, 0)
        self.spec_method_combo = QComboBox()
        self.spec_method_combo.addItem("Welch PSD", "welch")
        self.spec_method_combo.addItem("FFT Periodogram", "fft")
        self.spec_method_combo.currentIndexChanged.connect(self._on_spectrum_opts_changed)
        grid.addWidget(self.spec_method_combo, 0, 1)

        grid.addWidget(QLabel("Spectrum Scale:"), 1, 0)
        self.spec_scale_combo = QComboBox()
        self.spec_scale_combo.addItem("dB", True)
        self.spec_scale_combo.addItem("Linear", False)
        self.spec_scale_combo.currentIndexChanged.connect(self._on_spectrum_opts_changed)
        grid.addWidget(self.spec_scale_combo, 1, 1)

        lay.addLayout(grid)
        self._body_layout.addWidget(self.adv_plot_group)

    def _on_spectrum_opts_changed(self) -> None:
        if self._updating_ui:
            return
        method = str(self.spec_method_combo.currentData())
        use_db = bool(self.spec_scale_combo.currentData())
        self.spectrum_options_changed.emit(method, use_db)

    def set_spectrum_options(self, method: str, use_db: bool) -> None:
        self._updating_ui = True
        idx_m = self.spec_method_combo.findData(method.lower())
        if idx_m >= 0:
            self.spec_method_combo.setCurrentIndex(idx_m)
        idx_s = self.spec_scale_combo.findData(bool(use_db))
        if idx_s >= 0:
            self.spec_scale_combo.setCurrentIndex(idx_s)
        self._updating_ui = False

    def _set_enabled_all(self, enabled: bool) -> None:
        self.ch_combo.setEnabled(enabled)
        self.bulk_vis_row.setEnabled(enabled)
        self.derived_row.setEnabled(enabled)
        self.display_group.setEnabled(enabled)
        self.filters_group.setEnabled(enabled)
        self.events_group.setEnabled(enabled)

    def set_advanced_mode(self, advanced: bool) -> None:
        self._advanced_mode = advanced
        self._apply_mode_visibility()
        self._rebuild_filter_cards()

    def _apply_mode_visibility(self) -> None:
        adv = self._advanced_mode
        self.bulk_vis_row.setVisible(adv)
        self.derived_row.setVisible(adv)
        self.unit_row.setVisible(adv)
        ch = self._current_channel()
        has_lc = bool(ch and ch.derived_spec and ch.derived_spec.get("kind") == "linear_combination")
        self.derived_formula_lbl.setVisible(adv and has_lc)
        self.appearance_row.setVisible(adv)
        self.qrs_adv_box.setVisible(adv)
        self.adv_plot_group.setVisible(adv)

    def set_channels(
        self,
        channels: list[ChannelState],
        fs: float,
        filter_errors: dict[int, dict[str, str]] | None = None,
        selected_idx: int | None = None,
    ) -> None:
        self._channels = channels
        self._fs = fs
        self._filter_errors = filter_errors or {}

        if not channels:
            self._updating_ui = True
            self.ch_combo.clear()
            self._updating_ui = False
            self._set_enabled_all(False)
            return

        self._set_enabled_all(True)
        if selected_idx is not None and 0 <= selected_idx < len(channels):
            self._selected_idx = selected_idx
        elif self._selected_idx >= len(channels):
            self._selected_idx = 0

        self._refresh_channel_combo()
        self._populate_selected_channel()

    def update_filter_errors(self, filter_errors: dict[int, dict[str, str]]) -> None:
        self._filter_errors = filter_errors
        self._rebuild_filter_cards()

    def _refresh_channel_combo(self) -> None:
        self._updating_ui = True
        self.ch_combo.clear()
        for ch in self._channels:
            self.ch_combo.addItem(_swatch_icon(ch.color), ch.display_name, ch.index)
        self.ch_combo.setCurrentIndex(self._selected_idx)
        self._updating_ui = False

    def _current_channel(self) -> ChannelState | None:
        if 0 <= self._selected_idx < len(self._channels):
            return self._channels[self._selected_idx]
        return None

    def _populate_selected_channel(self) -> None:
        ch = self._current_channel()
        if ch is None:
            return
        self._updating_ui = True
        self.name_edit.setText(ch.display_name)
        self.unit_edit.setText(ch.unit)
        self.visible_cb.setChecked(ch.visible)
        self.color_btn.setStyleSheet(f"background-color: {ch.color}; border: 1px solid #6b7280;")
        self.width_spin.setValue(ch.line_width)

        is_derived = ch.derived_spec is not None
        is_linear = bool(ch.derived_spec and ch.derived_spec.get("kind") == "linear_combination")
        self.btn_edit_derived.setEnabled(is_linear)
        self.btn_del_derived.setEnabled(is_derived)
        if is_linear and ch.derived_spec is not None:
            names = [c.display_name for c in self._channels]
            self.derived_formula_lbl.setText(
                f"Formula: {format_linear_combination_formula(ch.derived_spec, names, ch.unit)}"
            )
            self.derived_formula_lbl.setVisible(self._advanced_mode)
        else:
            self.derived_formula_lbl.setText("")
            self.derived_formula_lbl.setVisible(False)

        self._update_scale_widgets(ch)

        # QRS detector state
        qrs_cfg = ch.detectors.get("qrs")
        defaults = DETECTOR_REGISTRY["qrs"].default_params
        if qrs_cfg is None:
            qrs_cfg = DetectorConfig("qrs", enabled=False, params=dict(defaults))
            ch.detectors["qrs"] = qrs_cfg

        self.qrs_cb.setChecked(qrs_cfg.enabled)
        p = {**defaults, **qrs_cfg.params}
        self.qrs_bp_cb.setChecked(bool(p["use_internal_bandpass"]))
        self.qrs_bp_lo.setValue(float(p["bp_low_hz"]))
        self.qrs_bp_hi.setValue(float(p["bp_high_hz"]))
        self.qrs_refrac.setValue(float(p["refractory_ms"]))
        self.qrs_thresh.setValue(float(p["threshold_factor"]))
        self.qrs_search.setValue(float(p["search_window_ms"]))
        self.qrs_polarity.setCurrentText(str(p["polarity"]))
        self._updating_ui = False

        self._rebuild_filter_cards()

    def _update_scale_widgets(self, ch: ChannelState) -> None:
        if ch.custom_y_scale:
            self.scale_status_lbl.setText("Y-Scale: Custom (Own Left Y-Axis)")
            self.autoscale_btn.setVisible(False)
            self.custom_scale_box.setVisible(True)
            r_lo = 0.0 if ch.raw_y_min is None else ch.raw_y_min
            r_hi = 1.0 if ch.raw_y_max is None else ch.raw_y_max
            f_lo = 0.0 if ch.filt_y_min is None else ch.filt_y_min
            f_hi = 1.0 if ch.filt_y_max is None else ch.filt_y_max
            self.raw_ymin_spin.setValue(r_lo)
            self.raw_ymax_spin.setValue(r_hi)
            self.filt_ymin_spin.setValue(f_lo)
            self.filt_ymax_spin.setValue(f_hi)
        else:
            self.scale_status_lbl.setText("Y-Scale: Shared (Original)")
            self.autoscale_btn.setVisible(True)
            self.custom_scale_box.setVisible(False)

    def refresh_scale_ui(self) -> None:
        ch = self._current_channel()
        if ch is None:
            return
        self._updating_ui = True
        self._update_scale_widgets(ch)
        self._updating_ui = False

    def _rebuild_filter_cards(self) -> None:
        while self.filter_cards_container.count():
            item = self.filter_cards_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        ch = self._current_channel()
        if ch is None:
            return

        ch_errs = self._filter_errors.get(ch.index, {})
        total = len(ch.filters)
        if total == 0:
            empty = QLabel("No filters added (output matches raw signal).")
            empty.setStyleSheet("color: #6b7280; font-size: 11px; font-style: italic;")
            self.filter_cards_container.addWidget(empty)
            return

        for idx, spec in enumerate(ch.filters):
            card = FilterCard(
                spec=spec,
                index=idx,
                total=total,
                fs=self._fs,
                advanced_mode=self._advanced_mode,
                error_msg=ch_errs.get(spec.id),
            )
            card.changed.connect(lambda c_idx=ch.index: self.channel_filters_changed.emit(c_idx))
            card.move_up.connect(self._on_move_filter_up)
            card.move_down.connect(self._on_move_filter_down)
            card.removed.connect(self._on_remove_filter)
            self.filter_cards_container.addWidget(card)

    def _on_channel_combo_changed(self, combo_idx: int) -> None:
        if self._updating_ui or combo_idx < 0:
            return
        self._selected_idx = combo_idx
        self._populate_selected_channel()
        self.channel_selected.emit(self._selected_idx)

    def _on_name_edited(self, text: str) -> None:
        ch = self._current_channel()
        if ch is None or self._updating_ui:
            return
        ch.display_name = text.strip() or ch.original_name
        self._updating_ui = True
        self.ch_combo.setItemText(self._selected_idx, ch.display_name)
        self._updating_ui = False
        self.channel_display_changed.emit()

    def _on_unit_edited(self, text: str) -> None:
        ch = self._current_channel()
        if ch is None or self._updating_ui:
            return
        ch.unit = text.strip()
        if ch.derived_spec and ch.derived_spec.get("kind") == "linear_combination":
            names = [c.display_name for c in self._channels]
            self.derived_formula_lbl.setText(
                f"Formula: {format_linear_combination_formula(ch.derived_spec, names, ch.unit)}"
            )
        self.channel_display_changed.emit()

    def _on_edit_derived_clicked(self) -> None:
        ch = self._current_channel()
        if ch is not None and ch.derived_spec and ch.derived_spec.get("kind") == "linear_combination":
            self.edit_derived_requested.emit(ch.index)

    def _on_delete_derived_clicked(self) -> None:
        ch = self._current_channel()
        if ch is not None and ch.derived_spec is not None:
            self.delete_derived_requested.emit(ch.index)

    def _on_visible_toggled(self, checked: bool) -> None:
        ch = self._current_channel()
        if ch is None or self._updating_ui:
            return
        ch.visible = checked
        self.channel_display_changed.emit()

    def _on_pick_color(self) -> None:
        ch = self._current_channel()
        if ch is None:
            return
        color = QColorDialog.getColor(QColor(ch.color), self, f"Color for {ch.display_name}")
        if color.isValid():
            ch.color = color.name()
            self.color_btn.setStyleSheet(f"background-color: {ch.color}; border: 1px solid #6b7280;")
            self._updating_ui = True
            self.ch_combo.setItemIcon(self._selected_idx, _swatch_icon(ch.color))
            self._updating_ui = False
            self.channel_display_changed.emit()

    def _on_width_changed(self, val: float) -> None:
        ch = self._current_channel()
        if ch is None or self._updating_ui:
            return
        ch.line_width = float(val)
        self.channel_display_changed.emit()

    def _on_autoscale_clicked(self) -> None:
        ch = self._current_channel()
        if ch is not None:
            self.autoscale_channel_requested.emit(ch.index)

    def _on_revert_clicked(self) -> None:
        ch = self._current_channel()
        if ch is not None:
            self.revert_scale_requested.emit(ch.index)

    def _on_custom_ylim_changed(self) -> None:
        ch = self._current_channel()
        if ch is None or self._updating_ui:
            return
        r_lo = self.raw_ymin_spin.value()
        r_hi = self.raw_ymax_spin.value()
        if r_hi <= r_lo:
            r_hi = r_lo + 1e-3
            self._updating_ui = True
            self.raw_ymax_spin.setValue(r_hi)
            self._updating_ui = False

        f_lo = self.filt_ymin_spin.value()
        f_hi = self.filt_ymax_spin.value()
        if f_hi <= f_lo:
            f_hi = f_lo + 1e-3
            self._updating_ui = True
            self.filt_ymax_spin.setValue(f_hi)
            self._updating_ui = False

        ch.raw_y_min = r_lo
        ch.raw_y_max = r_hi
        ch.filt_y_min = f_lo
        ch.filt_y_max = f_hi
        self.channel_display_changed.emit()

    def _on_add_filter(self) -> None:
        ch = self._current_channel()
        if ch is None:
            return
        ftype = str(self.add_filter_combo.currentData())
        spec = FilterSpec(filter_type=ftype, params=default_filter_params(ftype, self._fs))
        ch.filters.append(spec)
        self._rebuild_filter_cards()
        self.channel_filters_changed.emit(ch.index)

    def _on_move_filter_up(self, fid: str) -> None:
        ch = self._current_channel()
        if ch is None:
            return
        for i, f in enumerate(ch.filters):
            if f.id == fid and i > 0:
                ch.filters[i - 1], ch.filters[i] = ch.filters[i], ch.filters[i - 1]
                break
        self._rebuild_filter_cards()
        self.channel_filters_changed.emit(ch.index)

    def _on_move_filter_down(self, fid: str) -> None:
        ch = self._current_channel()
        if ch is None:
            return
        for i, f in enumerate(ch.filters):
            if f.id == fid and i < len(ch.filters) - 1:
                ch.filters[i + 1], ch.filters[i] = ch.filters[i], ch.filters[i + 1]
                break
        self._rebuild_filter_cards()
        self.channel_filters_changed.emit(ch.index)

    def _on_remove_filter(self, fid: str) -> None:
        ch = self._current_channel()
        if ch is None:
            return
        ch.filters = [f for f in ch.filters if f.id != fid]
        self._rebuild_filter_cards()
        self.channel_filters_changed.emit(ch.index)

    def _on_qrs_toggled(self, checked: bool) -> None:
        ch = self._current_channel()
        if ch is None or self._updating_ui:
            return
        if "qrs" not in ch.detectors:
            ch.detectors["qrs"] = DetectorConfig(
                "qrs", enabled=checked, params=dict(DETECTOR_REGISTRY["qrs"].default_params)
            )
        else:
            ch.detectors["qrs"].enabled = checked
        self.channel_detectors_changed.emit(ch.index)

    def _set_qrs_param(self, key: str, value: Any) -> None:
        ch = self._current_channel()
        if ch is None or self._updating_ui:
            return
        if "qrs" not in ch.detectors:
            ch.detectors["qrs"] = DetectorConfig(
                "qrs", enabled=self.qrs_cb.isChecked(), params=dict(DETECTOR_REGISTRY["qrs"].default_params)
            )
        ch.detectors["qrs"].params[key] = value
        if ch.detectors["qrs"].enabled:
            self.channel_detectors_changed.emit(ch.index)
