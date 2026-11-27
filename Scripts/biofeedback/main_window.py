"""Real-Time Biofeedback Studio PyQt6 + PyQtGraph main window."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .dsp_realtime import default_realtime_filter_params
from .engine import BiofeedbackEngine
from .lsl_io import LSLInputSource, SyntheticBioSource, discover_lsl_streams
from .models import (
    REALTIME_FILTER_DISPLAY_NAMES,
    REALTIME_FILTER_TYPES,
    CausalFilterSpec,
    PipelineConfig,
    default_ecg_filters,
    default_eda_filters,
)

# Disable costly software antialiasing for smooth real-time streaming plots.
pg.setConfigOptions(antialias=False)


class RealtimeFilterCard(QFrame):
    """Ordered card representing one causal filter stage in ECG or EDA stack."""

    changed = pyqtSignal()
    move_up = pyqtSignal(str)
    move_down = pyqtSignal(str)
    removed = pyqtSignal(str)

    def __init__(
        self,
        spec: CausalFilterSpec,
        index: int,
        total: int,
        fs: float,
        target: str,
        error_msg: str | None = None,
        dark_theme: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.spec = spec
        self.setObjectName("realtimeFilterCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(4)
        title = f"{index + 1}. {REALTIME_FILTER_DISPLAY_NAMES.get(spec.filter_type, spec.filter_type)}"
        self.enable_cb = QCheckBox(title)
        self.enable_cb.setChecked(spec.enabled)
        font = self.enable_cb.font()
        font.setBold(True)
        self.enable_cb.setFont(font)
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
        btn_del.setToolTip("Remove filter stage")
        btn_del.setStyleSheet("color: #dc2626; font-weight: bold;")
        btn_del.clicked.connect(lambda: self.removed.emit(self.spec.id))

        header.addWidget(btn_up)
        header.addWidget(btn_dn)
        header.addWidget(btn_del)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        max_freq = max(1.0, float(fs) * 0.499)
        step_hz = 0.1 if target == "eda" else 0.5
        row = 0
        ftype = spec.filter_type

        if ftype in {"lowpass", "highpass"}:
            grid.addWidget(QLabel("Cutoff (Hz):"), row, 0)
            self.cutoff_spin = self._freq_spin(
                float(spec.params.get("cutoff_hz", 10.0)), 0.01, max_freq, step_hz
            )
            self.cutoff_spin.valueChanged.connect(lambda v: self._set_param("cutoff_hz", v))
            grid.addWidget(self.cutoff_spin, row, 1)
            row += 1
        else:
            grid.addWidget(QLabel("Low (Hz):"), row, 0)
            self.low_spin = self._freq_spin(
                float(spec.params.get("low_hz", 5.0)), 0.01, max_freq, step_hz
            )
            self.low_spin.valueChanged.connect(lambda v: self._set_param("low_hz", v))
            grid.addWidget(self.low_spin, row, 1)
            row += 1

            grid.addWidget(QLabel("High (Hz):"), row, 0)
            self.high_spin = self._freq_spin(
                float(spec.params.get("high_hz", 35.0)), 0.01, max_freq, step_hz
            )
            self.high_spin.valueChanged.connect(lambda v: self._set_param("high_hz", v))
            grid.addWidget(self.high_spin, row, 1)
            row += 1

        grid.addWidget(QLabel("Order:"), row, 0)
        self.order_spin = QSpinBox()
        self.order_spin.setRange(1, 8)
        self.order_spin.setValue(int(spec.params.get("order", 4)))
        self.order_spin.valueChanged.connect(lambda v: self._set_param("order", v))
        grid.addWidget(self.order_spin, row, 1)
        root.addLayout(grid)

        self.err_lbl = QLabel("")
        self.err_lbl.setWordWrap(True)
        root.addWidget(self.err_lbl)

        self.apply_theme(dark_theme)
        self.set_error(error_msg)

    @staticmethod
    def _freq_spin(val: float, lo: float, hi: float, step: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(lo, max(lo + 0.1, hi))
        spin.setSingleStep(step)
        spin.setValue(float(val))
        return spin

    def _on_enable_toggled(self, checked: bool) -> None:
        self.spec.enabled = checked
        self.changed.emit()

    def _set_param(self, key: str, value: Any) -> None:
        self.spec.params[key] = value
        self.changed.emit()

    def set_error(self, error_msg: str | None) -> None:
        if error_msg:
            self.err_lbl.setText(error_msg)
            self.err_lbl.setVisible(True)
        else:
            self.err_lbl.setText("")
            self.err_lbl.setVisible(False)

    def apply_theme(self, dark: bool) -> None:
        if dark:
            bg, border, fg = "#26292e", "#3f434a", "#f3f4f6"
            err_fg = "#f87171"
        else:
            bg, border, fg = "#f9fafb", "#d1d5db", "#111827"
            err_fg = "#b91c1c"
        self.setStyleSheet(
            f"#realtimeFilterCard {{ background-color: {bg}; border: 1px solid {border}; border-radius: 6px; }}\n"
            f"#realtimeFilterCard QLabel {{ color: {fg}; background: transparent; }}"
        )
        pal = QPalette(self.enable_cb.palette())
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
            pal.setColor(role, QColor(fg))
        self.enable_cb.setPalette(pal)
        self.err_lbl.setStyleSheet(f"color: {err_fg}; font-size: 11px; background: transparent;")


class BiofeedbackWindow(QMainWindow):
    """Real-time biosignal viewer, template calibrator, and LSL feature broadcaster."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        show_signals: bool = True,
        show_features: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("BCI Workshop — Real-Time Biofeedback Studio")
        self.resize(1340, 840)

        self._engine = BiofeedbackEngine(PipelineConfig())
        self._discovered_streams: list[dict[str, Any]] = []
        self._fs: float = 250.0
        self._last_cal_version: int = 0
        self._last_dir = Path.cwd()
        self._filter_cards: dict[str, RealtimeFilterCard] = {}
        self._active_input_stream_name: str = "SyntheticBioSignals"
        self._rec_proc: subprocess.Popen[str] | None = None
        self._rec_dir: Path | None = None
        self._rec_stats: dict[str, Any] = {}
        self._rec_reader_thread: threading.Thread | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_ui_tick)

        self._build_ui(show_signals=show_signals, show_features=show_features)
        self._apply_theme_styles()
        self._refresh_streams(initial=True)
        self._update_template_plot()
        self._rebuild_filter_cards()
        self._on_adv_target_changed()
        self._on_vis_toggled()

    @property
    def cfg(self) -> PipelineConfig:
        return self._engine.cfg

    @cfg.setter
    def cfg(self, value: PipelineConfig) -> None:
        self._engine.cfg = value

    @property
    def _running(self) -> bool:
        return self._engine.running

    def _build_ui(self, *, show_signals: bool, show_features: bool) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # --- Top Control Bar (2 clean rows, scoped styling for OS light/dark safety) ---
        self._top_bar = QFrame()
        self._top_bar.setObjectName("topControlBar")
        top_lay = QVBoxLayout(self._top_bar)
        top_lay.setContentsMargins(10, 8, 10, 8)
        top_lay.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QLabel("Input Stream:"))
        self.stream_combo = QComboBox()
        self.stream_combo.setMinimumWidth(220)
        self.stream_combo.currentIndexChanged.connect(self._on_stream_selected)
        row1.addWidget(self.stream_combo)

        self.refresh_btn = QPushButton("Refresh LSL")
        self.refresh_btn.clicked.connect(lambda: self._refresh_streams(initial=False))
        row1.addWidget(self.refresh_btn)

        row1.addWidget(QLabel("ECG Ch:"))
        self.ecg_ch_combo = QComboBox()
        self.ecg_ch_combo.currentIndexChanged.connect(self._on_channel_changed)
        row1.addWidget(self.ecg_ch_combo)

        row1.addWidget(QLabel("EDA Ch:"))
        self.eda_ch_combo = QComboBox()
        self.eda_ch_combo.currentIndexChanged.connect(self._on_channel_changed)
        row1.addWidget(self.eda_ch_combo)

        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._toggle_start)
        row1.addWidget(self.start_btn)

        self.cal_btn = QPushButton("Calibrate (15s)")
        self.cal_btn.setEnabled(False)
        self.cal_btn.clicked.connect(self.start_calibration)
        row1.addWidget(self.cal_btn)

        self.rec_btn = QPushButton("Record")
        self.rec_btn.setEnabled(False)
        self.rec_btn.setToolTip("Record raw input, filtered outputs, features, and events via separate LSL process.")
        self.rec_btn.clicked.connect(self._toggle_record)
        row1.addWidget(self.rec_btn)

        self.cal_bar = QProgressBar()
        self.cal_bar.setFixedWidth(110)
        self.cal_bar.setVisible(False)
        row1.addWidget(self.cal_bar)

        self.save_cal_btn = QPushButton("Save Cal")
        self.save_cal_btn.clicked.connect(self._on_save_cal_clicked)
        row1.addWidget(self.save_cal_btn)

        self.load_cal_btn = QPushButton("Load Cal")
        self.load_cal_btn.clicked.connect(self._on_load_cal_clicked)
        row1.addWidget(self.load_cal_btn)
        row1.addStretch(1)
        top_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        self.lsl_out_cb = QCheckBox("Broadcast LSL")
        self.lsl_out_cb.setChecked(True)
        self.lsl_out_cb.toggled.connect(self._on_broadcast_toggled)
        row2.addWidget(self.lsl_out_cb)

        self.cb_show_signals = QCheckBox("Show Signals")
        self.cb_show_signals.setChecked(bool(show_signals))
        self.cb_show_signals.setToolTip(
            "Toggle live ECG & EDA waveform traces on the graphs (detected feature overlays remain independent)."
        )
        self.cb_show_signals.toggled.connect(self._on_vis_toggled)
        row2.addWidget(self.cb_show_signals)

        self.cb_show_features = QCheckBox("Show Features")
        self.cb_show_features.setChecked(bool(show_features))
        self.cb_show_features.setToolTip(
            "Toggle R-peak markers, SCR markers, and EDA tonic baseline overlays on the graphs."
        )
        self.cb_show_features.toggled.connect(self._on_vis_toggled)
        row2.addWidget(self.cb_show_features)

        self.cb_advanced = QCheckBox("Advanced")
        self.cb_advanced.setChecked(False)
        self.cb_advanced.setToolTip("Show fine-grained causal filter stack & QRS/Tonic parameters.")
        self.cb_advanced.toggled.connect(self._on_advanced_toggled)
        row2.addWidget(self.cb_advanced)

        row2.addStretch(1)
        top_lay.addLayout(row2)
        root.addWidget(self._top_bar)

        # --- Main Splitter ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # Left Pane: Stacked ECG & EDA Real-Time Multi-Res Plots + Paused Placeholder
        self.left_w = QWidget()
        left_lay = QVBoxLayout(self.left_w)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)

        self.signals_paused_lbl = QLabel(
            "Graph visualization is OFF.\nReal-time DSP and LSL broadcasting continue in the background."
        )
        self.signals_paused_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.signals_paused_lbl.setWordWrap(True)
        self.signals_paused_lbl.setVisible(False)
        left_lay.addWidget(self.signals_paused_lbl, 1)

        self.ecg_plot = pg.PlotWidget(background="w")
        self.ecg_plot.setTitle("ECG — Causal Filtered (Multi-Resolution Window)", color="#1f2937")
        self.ecg_plot.showGrid(x=True, y=True, alpha=0.2)
        self.ecg_plot.setLabel("left", "ECG (filtered)")
        self.ecg_plot.setLabel("bottom", "Time (s)")
        self.ecg_curve = self.ecg_plot.plot(pen=pg.mkPen("#1d4ed8", width=1.4))
        self.ecg_scatter = pg.ScatterPlotItem(
            symbol="t", size=10, brush=pg.mkBrush("#dc2626"), pen=pg.mkPen("#111827")
        )
        self.ecg_plot.addItem(self.ecg_scatter)
        left_lay.addWidget(self.ecg_plot, 1)

        self.eda_plot = pg.PlotWidget(background="w")
        self.eda_plot.setTitle("EDA — Causal Filtered + Tonic Baseline (Multi-Resolution Window)", color="#1f2937")
        self.eda_plot.showGrid(x=True, y=True, alpha=0.2)
        self.eda_plot.setLabel("left", f"EDA ({self.cfg.eda_unit})")
        self.eda_plot.setLabel("bottom", "Time (s)")
        self.eda_curve = self.eda_plot.plot(pen=pg.mkPen("#059669", width=1.6))
        self.eda_tonic_curve = self.eda_plot.plot(
            pen=pg.mkPen("#6b7280", width=1.4, style=Qt.PenStyle.DashLine)
        )
        self.eda_scatter = pg.ScatterPlotItem(
            symbol="o", size=10, brush=pg.mkBrush("#d97706"), pen=pg.mkPen("#111827")
        )
        self.eda_plot.addItem(self.eda_scatter)
        self.eda_plot.setXLink(self.ecg_plot)
        left_lay.addWidget(self.eda_plot, 1)

        splitter.addWidget(self.left_w)

        # Right Pane: Scrollable Metrics, Templates & Settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(340)
        scroll.setMaximumWidth(450)
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(6, 0, 6, 6)
        right_lay.setSpacing(10)
        scroll.setWidget(right_w)

        # KPI Cards (always visible on control panel)
        self.kpi_box = QGroupBox("Live Biofeedback Metrics (Rolling Windows)")
        kpi_grid = QGridLayout(self.kpi_box)
        self.lbl_bpm = self._kpi_label("-- BPM")
        self.lbl_tonic = self._kpi_label("--")
        self.lbl_phasic = self._kpi_label("--")
        self.lbl_scr_rate = self._kpi_label("-- /min")
        self.lbl_last_ev = QLabel("Last Event: —")
        self.lbl_last_ev.setWordWrap(True)

        kpi_grid.addWidget(QLabel("Rolling BPM:"), 0, 0)
        kpi_grid.addWidget(self.lbl_bpm, 0, 1)
        kpi_grid.addWidget(QLabel("EDA Tonic:"), 1, 0)
        kpi_grid.addWidget(self.lbl_tonic, 1, 1)
        kpi_grid.addWidget(QLabel("EDA Phasic:"), 2, 0)
        kpi_grid.addWidget(self.lbl_phasic, 2, 1)
        kpi_grid.addWidget(QLabel("SCR Rate:"), 3, 0)
        kpi_grid.addWidget(self.lbl_scr_rate, 3, 1)
        kpi_grid.addWidget(self.lbl_last_ev, 4, 0, 1, 2)
        right_lay.addWidget(self.kpi_box)

        # Learned Templates Preview (always visible on control panel)
        self.tmpl_box = QGroupBox("Calibrated Templates (Prior-Blended)")
        tmpl_lay = QVBoxLayout(self.tmpl_box)
        self.tmpl_plot = pg.PlotWidget(background="w")
        self.tmpl_plot.setMinimumHeight(140)
        self.tmpl_plot.setMaximumHeight(170)
        self.tmpl_plot.showGrid(x=True, y=True, alpha=0.2)
        self.tmpl_plot.setLabel("bottom", "Normalized Template Window")
        self.tmpl_ecg_curve = self.tmpl_plot.plot(pen=pg.mkPen("#1d4ed8", width=1.8), name="QRS")
        self.tmpl_eda_curve = self.tmpl_plot.plot(pen=pg.mkPen("#059669", width=1.8), name="SCR")
        tmpl_lay.addWidget(self.tmpl_plot)
        self.lbl_cal_status = QLabel("Status: Using canonical priors (uncalibrated)")
        self.lbl_cal_status.setWordWrap(True)
        tmpl_lay.addWidget(self.lbl_cal_status)
        right_lay.addWidget(self.tmpl_box)

        # Pipeline Settings
        cfg_box = QGroupBox("Pipeline & Calibration Settings")
        form = QFormLayout(cfg_box)

        self.spin_disp_win = self._dspin(self.cfg.display_window_s, 5.0, 120.0, 1.0, 1)
        self.spin_disp_win.valueChanged.connect(self._on_cfg_changed)
        form.addRow("Display Window (s):", self.spin_disp_win)

        self.spin_bpm_win = self._dspin(self.cfg.bpm_window_s, 3.0, 60.0, 1.0, 1)
        self.spin_bpm_win.valueChanged.connect(self._on_cfg_changed)
        form.addRow("BPM Rolling Win (s):", self.spin_bpm_win)

        self.spin_scr_win = self._dspin(self.cfg.scr_window_s, 10.0, 120.0, 5.0, 1)
        self.spin_scr_win.valueChanged.connect(self._on_cfg_changed)
        form.addRow("SCR Rolling Win (s):", self.spin_scr_win)

        self.cb_notch = QCheckBox("50 Hz Mains Notch (ECG)")
        self.cb_notch.setChecked(self.cfg.ecg_notch_50hz)
        self.cb_notch.toggled.connect(self._on_filter_cfg_changed)
        form.addRow(self.cb_notch)

        self.spin_eda_m = self._dspin(self.cfg.eda_scale_m, -1e6, 1e6, 0.01, 5)
        self.spin_eda_m.valueChanged.connect(self._on_cfg_changed)
        form.addRow("EDA Scale (m):", self.spin_eda_m)

        self.spin_eda_b = self._dspin(self.cfg.eda_offset_b, -1e6, 1e6, 0.1, 4)
        self.spin_eda_b.valueChanged.connect(self._on_cfg_changed)
        form.addRow("EDA Offset (b):", self.spin_eda_b)

        self.edit_eda_unit = QLineEdit(self.cfg.eda_unit)
        self.edit_eda_unit.textChanged.connect(self._on_cfg_changed)
        form.addRow("EDA Unit:", self.edit_eda_unit)

        self.cb_eda_inv = QCheckBox("Reciprocal (num / (m·x + b))")
        self.cb_eda_inv.setChecked(self.cfg.eda_invert)
        self.cb_eda_inv.toggled.connect(self._on_cfg_changed)
        form.addRow(self.cb_eda_inv)

        self.spin_eda_num = self._dspin(self.cfg.eda_invert_num, -1e12, 1e12, 1000.0, 1)
        self.spin_eda_num.valueChanged.connect(self._on_cfg_changed)
        form.addRow("Reciprocal Num:", self.spin_eda_num)

        self.cb_online_adapt = QCheckBox("Online Template Adaptation (EMA)")
        self.cb_online_adapt.setChecked(self.cfg.online_adapt)
        self.cb_online_adapt.toggled.connect(self._on_cfg_changed)
        form.addRow(self.cb_online_adapt)

        right_lay.addWidget(cfg_box)

        # Advanced Filters Box (hidden unless cb_advanced is checked)
        self.adv_filter_box = QGroupBox("Advanced Filters (Causal Real-Time)")
        adv_lay = QVBoxLayout(self.adv_filter_box)
        adv_lay.setSpacing(8)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Signal:"))
        self.adv_target_combo = QComboBox()
        self.adv_target_combo.addItem("ECG", "ecg")
        self.adv_target_combo.addItem("EDA", "eda")
        self.adv_target_combo.currentIndexChanged.connect(self._on_adv_target_changed)
        target_row.addWidget(self.adv_target_combo, 1)
        adv_lay.addLayout(target_row)

        add_row = QHBoxLayout()
        self.adv_add_type_combo = QComboBox()
        for ftype in REALTIME_FILTER_TYPES:
            self.adv_add_type_combo.addItem(REALTIME_FILTER_DISPLAY_NAMES[ftype], ftype)
        self.adv_add_btn = QPushButton("+ Add")
        self.adv_add_btn.clicked.connect(self._on_add_filter_clicked)
        self.adv_reset_btn = QPushButton("Reset")
        self.adv_reset_btn.clicked.connect(self._on_reset_filters_clicked)
        add_row.addWidget(self.adv_add_type_combo, 1)
        add_row.addWidget(self.adv_add_btn)
        add_row.addWidget(self.adv_reset_btn)
        adv_lay.addLayout(add_row)

        self._filter_cards_container = QWidget()
        self._filter_cards_lay = QVBoxLayout(self._filter_cards_container)
        self._filter_cards_lay.setContentsMargins(0, 0, 0, 0)
        self._filter_cards_lay.setSpacing(6)
        adv_lay.addWidget(self._filter_cards_container)

        # ECG detector bandpass fine-tuning
        self.adv_ecg_extra = QGroupBox("QRS Detector Bandpass")
        qrs_form = QFormLayout(self.adv_ecg_extra)
        self.spin_qrs_low = self._dspin(self.cfg.ecg_qrs_low_hz, 0.1, 40.0, 0.5, 2)
        self.spin_qrs_low.valueChanged.connect(self._on_advanced_extra_changed)
        qrs_form.addRow("QRS Low (Hz):", self.spin_qrs_low)
        self.spin_qrs_high = self._dspin(self.cfg.ecg_qrs_high_hz, 1.0, 120.0, 0.5, 2)
        self.spin_qrs_high.valueChanged.connect(self._on_advanced_extra_changed)
        qrs_form.addRow("QRS High (Hz):", self.spin_qrs_high)
        self.spin_qrs_order = QSpinBox()
        self.spin_qrs_order.setRange(1, 8)
        self.spin_qrs_order.setValue(int(self.cfg.ecg_qrs_order))
        self.spin_qrs_order.valueChanged.connect(self._on_advanced_extra_changed)
        qrs_form.addRow("QRS Order:", self.spin_qrs_order)
        adv_lay.addWidget(self.adv_ecg_extra)

        # EDA tonic baseline fine-tuning
        self.adv_eda_extra = QGroupBox("EDA Tonic Baseline Lowpass")
        tonic_form = QFormLayout(self.adv_eda_extra)
        self.spin_tonic_lp = self._dspin(self.cfg.eda_tonic_lp_hz, 0.005, 1.0, 0.005, 3)
        self.spin_tonic_lp.valueChanged.connect(self._on_advanced_extra_changed)
        tonic_form.addRow("Tonic LP (Hz):", self.spin_tonic_lp)
        self.spin_tonic_order = QSpinBox()
        self.spin_tonic_order.setRange(1, 6)
        self.spin_tonic_order.setValue(int(self.cfg.eda_tonic_order))
        self.spin_tonic_order.valueChanged.connect(self._on_advanced_extra_changed)
        tonic_form.addRow("Tonic Order:", self.spin_tonic_order)
        adv_lay.addWidget(self.adv_eda_extra)

        self.adv_filter_box.setVisible(False)
        right_lay.addWidget(self.adv_filter_box)

        right_lay.addStretch(1)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self.status_lbl = QLabel("Idle — select an LSL stream (or Synthetic simulator) and click Start.")
        self.statusBar().addWidget(self.status_lbl, 1)

    def _is_dark_theme(self) -> bool:
        return self.palette().color(QPalette.ColorRole.Window).lightness() < 128

    def _apply_theme_styles(self) -> None:
        dark = self._is_dark_theme()
        if dark:
            bar_bg, bar_border, bar_fg = "#2b2d30", "#43464b", "#f3f4f6"
            cb_box_bg = "#1e2024"
            muted_fg = "#9ca3af"
            paused_bg, paused_border = "#1f2226", "#374151"
            c_bpm, c_tonic, c_phasic, c_scr = "#f87171", "#34d399", "#fbbf24", "#a78bfa"
        else:
            bar_bg, bar_border, bar_fg = "#f3f4f6", "#d1d5db", "#111827"
            cb_box_bg = "#ffffff"
            muted_fg = "#4b5563"
            paused_bg, paused_border = "#f9fafb", "#e5e7eb"
            c_bpm, c_tonic, c_phasic, c_scr = "#dc2626", "#059669", "#d97706", "#7c3aed"

        # Scope QSS only to frame and labels (avoid QCheckBox QSS so Fusion renders crisp native indicators)
        self._top_bar.setStyleSheet(
            f"#topControlBar {{ background-color: {bar_bg}; border: 1px solid {bar_border}; border-radius: 6px; }}\n"
            f"#topControlBar QLabel {{ color: {bar_fg}; background: transparent; }}"
        )
        for cb in (self.lsl_out_cb, self.cb_show_signals, self.cb_show_features, self.cb_advanced):
            pal = QPalette(self.palette())
            pal.setColor(QPalette.ColorRole.WindowText, QColor(bar_fg))
            pal.setColor(QPalette.ColorRole.Text, QColor(bar_fg))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor(bar_fg))
            pal.setColor(QPalette.ColorRole.Base, QColor(cb_box_bg))
            pal.setColor(QPalette.ColorRole.Mid, QColor(muted_fg))
            cb.setPalette(pal)

        self.start_btn.setStyleSheet(
            "background: #2563eb; color: #ffffff; font-weight: 600; padding: 5px 14px; border-radius: 4px;"
        )
        self.lbl_last_ev.setStyleSheet(f"color: {muted_fg}; font-size: 11px;")
        self.lbl_cal_status.setStyleSheet(f"color: {muted_fg}; font-size: 11px;")
        self.signals_paused_lbl.setStyleSheet(
            f"color: {muted_fg}; background-color: {paused_bg}; border: 1px dashed {paused_border}; "
            "border-radius: 6px; padding: 24px; font-size: 13px;"
        )
        self.lbl_bpm.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {c_bpm};")
        self.lbl_tonic.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {c_tonic};")
        self.lbl_phasic.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {c_phasic};")
        self.lbl_scr_rate.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {c_scr};")

        for card in self._filter_cards.values():
            card.apply_theme(dark)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._apply_theme_styles()
        super().changeEvent(event)

    @staticmethod
    def _kpi_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 16px; font-weight: 700;")
        return lbl

    @staticmethod
    def _dspin(val: float, lo: float, hi: float, step: float, dec: int) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(dec)
        s.setValue(float(val))
        return s

    def _refresh_streams(self, initial: bool = False) -> None:
        self.stream_combo.blockSignals(True)
        self.stream_combo.clear()
        self.stream_combo.addItem("[Synthetic ECG + EDA (250 Hz)]", -1)
        self._discovered_streams = []
        if not initial:
            try:
                self._discovered_streams = discover_lsl_streams(timeout_s=0.8)
            except Exception as exc:
                self.status_lbl.setText(f"LSL discovery error: {exc}")
        for idx, meta in enumerate(self._discovered_streams):
            lbl = f"{meta['name']} ({meta['type']}, {meta['channel_count']} ch, {meta['nominal_srate']:.0f} Hz)"
            self.stream_combo.addItem(lbl, idx)
        self.stream_combo.blockSignals(False)
        self._on_stream_selected(self.stream_combo.currentIndex())

    def _on_stream_selected(self, _idx: int) -> None:
        data_idx = self.stream_combo.currentData()
        if data_idx is None or int(data_idx) < 0:
            labels = ["Ch1: ECG (Sim)", "Ch2: EDA (Sim)"]
        else:
            meta = self._discovered_streams[int(data_idx)]
            labels = [f"Ch{i + 1}" for i in range(meta["channel_count"])]

        self.ecg_ch_combo.blockSignals(True)
        self.eda_ch_combo.blockSignals(True)
        self.ecg_ch_combo.clear()
        self.eda_ch_combo.clear()
        self.ecg_ch_combo.addItem("None", -1)
        self.eda_ch_combo.addItem("None", -1)
        for i, lbl in enumerate(labels):
            self.ecg_ch_combo.addItem(lbl, i)
            self.eda_ch_combo.addItem(lbl, i)
        if len(labels) >= 1:
            self.ecg_ch_combo.setCurrentIndex(1)
        if len(labels) >= 2:
            self.eda_ch_combo.setCurrentIndex(2)
        self.ecg_ch_combo.blockSignals(False)
        self.eda_ch_combo.blockSignals(False)
        self._on_channel_changed()

    def _selected_channels(self) -> tuple[int, int]:
        ecg_data = self.ecg_ch_combo.currentData()
        eda_data = self.eda_ch_combo.currentData()
        ecg_idx = int(ecg_data) if ecg_data is not None else -1
        eda_idx = int(eda_data) if eda_data is not None else -1
        return ecg_idx, eda_idx

    def _on_channel_changed(self) -> None:
        self._engine.set_channels(*self._selected_channels())

    def _on_broadcast_toggled(self, checked: bool) -> None:
        self._engine.set_broadcast_lsl(checked)
        self._update_status_text()

    def _update_timer_interval(self) -> None:
        show_sig = self.cb_show_signals.isChecked()
        show_feat = self.cb_show_features.isChecked()
        if show_sig:
            self._timer.setInterval(50)  # 20 Hz waveform + overlays
        elif show_feat:
            self._timer.setInterval(100)  # 10 Hz feature-only graph overlays
        else:
            self._timer.setInterval(250)  # 4 Hz control-panel KPIs & status

    def _on_vis_toggled(self) -> None:
        show_sig = self.cb_show_signals.isChecked()
        show_feat = self.cb_show_features.isChecked()
        self._engine.set_display_recording(signals=show_sig, features=show_feat)

        # Signals toggle controls waveform traces on the plots
        self.ecg_curve.setVisible(show_sig)
        self.eda_curve.setVisible(show_sig)
        if not show_sig:
            self.ecg_curve.setData([], [])
            self.eda_curve.setData([], [])

        # Features toggle controls event markers & tonic baseline overlay on the plots
        self.ecg_scatter.setVisible(show_feat)
        self.eda_scatter.setVisible(show_feat)
        self.eda_tonic_curve.setVisible(show_feat)
        if not show_feat:
            self.ecg_scatter.setData(x=[], y=[])
            self.eda_scatter.setData(x=[], y=[])
            self.eda_tonic_curve.setData([], [])

        # Keep graphs visible whenever either signals or features are shown
        any_graph = show_sig or show_feat
        self.ecg_plot.setVisible(any_graph)
        self.eda_plot.setVisible(any_graph)
        self.signals_paused_lbl.setVisible(not any_graph)

        self._update_timer_interval()
        if self._running:
            self._render_ui()

    def _on_advanced_toggled(self, checked: bool) -> None:
        self.adv_filter_box.setVisible(bool(checked))

    def _current_filter_target(self) -> str:
        return str(self.adv_target_combo.currentData() or "ecg")

    def _current_filter_list(self, target: str | None = None) -> list[CausalFilterSpec]:
        tgt = target or self._current_filter_target()
        return self.cfg.eda_filters if tgt == "eda" else self.cfg.ecg_filters

    def _on_adv_target_changed(self) -> None:
        tgt = self._current_filter_target()
        self.adv_ecg_extra.setVisible(tgt == "ecg")
        self.adv_eda_extra.setVisible(tgt == "eda")
        self._rebuild_filter_cards()

    def _rebuild_filter_cards(self) -> None:
        while self._filter_cards_lay.count():
            item = self._filter_cards_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._filter_cards.clear()

        tgt = self._current_filter_target()
        specs = self._current_filter_list(tgt)
        errs = self._engine.get_filter_errors().get(tgt, {})
        dark = self._is_dark_theme()

        for idx, spec in enumerate(specs):
            card = RealtimeFilterCard(
                spec=spec,
                index=idx,
                total=len(specs),
                fs=self._fs,
                target=tgt,
                error_msg=errs.get(spec.id),
                dark_theme=dark,
                parent=self._filter_cards_container,
            )
            card.changed.connect(self._on_filter_stack_params_changed)
            card.move_up.connect(lambda fid: self._move_filter(fid, -1))
            card.move_down.connect(lambda fid: self._move_filter(fid, 1))
            card.removed.connect(self._remove_filter)
            self._filter_cards[spec.id] = card
            self._filter_cards_lay.addWidget(card)

    def _refresh_card_errors(self) -> None:
        tgt = self._current_filter_target()
        errs = self._engine.get_filter_errors().get(tgt, {})
        for fid, card in self._filter_cards.items():
            card.set_error(errs.get(fid))

    def _apply_advanced_filters(self, target: str) -> None:
        self._engine.update_config(
            self.cfg,
            rebuild_ecg_filter=(target == "ecg"),
            rebuild_eda_filter=(target == "eda"),
        )
        self._refresh_card_errors()

    def _on_filter_stack_params_changed(self) -> None:
        self._apply_advanced_filters(self._current_filter_target())

    def _on_add_filter_clicked(self) -> None:
        tgt = self._current_filter_target()
        ftype = str(self.adv_add_type_combo.currentData() or "lowpass")
        params = default_realtime_filter_params(ftype, self._fs)
        self._current_filter_list(tgt).append(CausalFilterSpec(filter_type=ftype, params=params))
        self._apply_advanced_filters(tgt)
        self._rebuild_filter_cards()

    def _on_reset_filters_clicked(self) -> None:
        tgt = self._current_filter_target()
        if tgt == "eda":
            self.cfg.eda_filters = default_eda_filters(self.cfg.eda_lp_hz)
        else:
            self.cfg.ecg_filters = default_ecg_filters(self.cfg.ecg_bp_low_hz, self.cfg.ecg_bp_high_hz)
        self._apply_advanced_filters(tgt)
        self._rebuild_filter_cards()

    def _move_filter(self, fid: str, delta: int) -> None:
        tgt = self._current_filter_target()
        specs = self._current_filter_list(tgt)
        for i, s in enumerate(specs):
            if s.id == fid:
                j = i + delta
                if 0 <= j < len(specs):
                    specs[i], specs[j] = specs[j], specs[i]
                    self._apply_advanced_filters(tgt)
                    self._rebuild_filter_cards()
                return

    def _remove_filter(self, fid: str) -> None:
        tgt = self._current_filter_target()
        specs = self._current_filter_list(tgt)
        for i, s in enumerate(specs):
            if s.id == fid:
                specs.pop(i)
                self._apply_advanced_filters(tgt)
                self._rebuild_filter_cards()
                return

    def _on_advanced_extra_changed(self) -> None:
        cfg = self._engine.cfg
        qrs_lo = float(self.spin_qrs_low.value())
        qrs_hi = float(self.spin_qrs_high.value())
        if qrs_hi <= qrs_lo:
            qrs_hi = qrs_lo + 1.0
        cfg.ecg_qrs_low_hz = qrs_lo
        cfg.ecg_qrs_high_hz = qrs_hi
        cfg.ecg_qrs_order = int(self.spin_qrs_order.value())

        cfg.eda_tonic_lp_hz = float(self.spin_tonic_lp.value())
        cfg.eda_tonic_order = int(self.spin_tonic_order.value())
        self._engine.update_config(cfg, rebuild_ecg_filter=True, rebuild_eda_filter=True)

    def _on_cfg_changed(self) -> None:
        cfg = self._engine.cfg
        cfg.display_window_s = float(self.spin_disp_win.value())
        cfg.bpm_window_s = float(self.spin_bpm_win.value())
        cfg.scr_window_s = float(self.spin_scr_win.value())
        cfg.eda_scale_m = float(self.spin_eda_m.value())
        cfg.eda_offset_b = float(self.spin_eda_b.value())
        cfg.eda_unit = self.edit_eda_unit.text().strip() or "μS"
        cfg.eda_invert = self.cb_eda_inv.isChecked()
        cfg.eda_invert_num = float(self.spin_eda_num.value())
        cfg.online_adapt = self.cb_online_adapt.isChecked()

        self._engine.update_config(cfg, rebuild_ecg_filter=False)
        self.eda_plot.setLabel("left", f"EDA ({cfg.eda_unit})")

    def _on_filter_cfg_changed(self) -> None:
        cfg = self._engine.cfg
        cfg.ecg_notch_50hz = self.cb_notch.isChecked()
        self._engine.update_config(cfg, rebuild_ecg_filter=True)

    def _toggle_start(self) -> None:
        if self._engine.running:
            self.stop_stream()
        else:
            self.start_stream()

    def select_stream_by_name(self, stream_name: str, refresh: bool = True) -> bool:
        """Select either 'synthetic' or a discovered LSL stream by exact/partial name."""
        target = stream_name.strip().lower()
        if not target or target == "synthetic":
            self.stream_combo.setCurrentIndex(0)
            return True
        if refresh:
            self._refresh_streams(initial=False)
        for idx, meta in enumerate(self._discovered_streams):
            if target in meta["name"].lower():
                combo_idx = self.stream_combo.findData(idx)
                if combo_idx >= 0:
                    self.stream_combo.setCurrentIndex(combo_idx)
                    return True
        return False

    def select_channels(self, ecg_ch: int, eda_ch: int) -> None:
        """Select 0-based channel indices (or -1 for None) in UI combos."""
        idx_e = self.ecg_ch_combo.findData(int(ecg_ch))
        if idx_e >= 0:
            self.ecg_ch_combo.setCurrentIndex(idx_e)
        idx_d = self.eda_ch_combo.findData(int(eda_ch))
        if idx_d >= 0:
            self.eda_ch_combo.setCurrentIndex(idx_d)
        self._on_channel_changed()

    def start_stream(self, *, start_thread: bool = True) -> None:
        if self._engine.running:
            return
        data_idx = self.stream_combo.currentData()
        try:
            if data_idx is None or int(data_idx) < 0:
                source: LSLInputSource | SyntheticBioSource = SyntheticBioSource(fs=250.0)
                self._fs = 250.0
                self._active_input_stream_name = "SyntheticBioSignals"
            else:
                meta = self._discovered_streams[int(data_idx)]
                source = LSLInputSource(meta["info"])
                self._fs = meta["nominal_srate"] if meta["nominal_srate"] > 0 else 250.0
                self._active_input_stream_name = str(meta["name"])
                if len(source.channel_labels) == source.channel_count:
                    cur_ecg = self.ecg_ch_combo.currentData()
                    cur_eda = self.eda_ch_combo.currentData()
                    self.ecg_ch_combo.blockSignals(True)
                    self.eda_ch_combo.blockSignals(True)
                    self.ecg_ch_combo.clear()
                    self.eda_ch_combo.clear()
                    self.ecg_ch_combo.addItem("None", -1)
                    self.eda_ch_combo.addItem("None", -1)
                    for i, lbl in enumerate(source.channel_labels):
                        self.ecg_ch_combo.addItem(f"Ch{i + 1}: {lbl}", i)
                        self.eda_ch_combo.addItem(f"Ch{i + 1}: {lbl}", i)
                    if cur_ecg is not None:
                        idx_e = self.ecg_ch_combo.findData(cur_ecg)
                        if idx_e >= 0:
                            self.ecg_ch_combo.setCurrentIndex(idx_e)
                    if cur_eda is not None:
                        idx_d = self.eda_ch_combo.findData(cur_eda)
                        if idx_d >= 0:
                            self.eda_ch_combo.setCurrentIndex(idx_d)
                    self.ecg_ch_combo.blockSignals(False)
                    self.eda_ch_combo.blockSignals(False)
        except Exception as exc:
            QMessageBox.critical(self, "Stream Error", f"Could not open input stream:\n{exc}")
            return

        ecg_idx, eda_idx = self._selected_channels()
        self._engine.set_display_recording(
            signals=self.cb_show_signals.isChecked(),
            features=self.cb_show_features.isChecked(),
        )
        self._engine.start(
            source,
            fs=self._fs,
            ecg_ch_idx=ecg_idx,
            eda_ch_idx=eda_idx,
            broadcast_lsl=self.lsl_out_cb.isChecked(),
            start_thread=start_thread,
        )

        self.start_btn.setText("Stop")
        self.cal_btn.setEnabled(True)
        self.rec_btn.setEnabled(True)
        self.stream_combo.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self._rebuild_filter_cards()
        self._update_timer_interval()
        self._timer.start()
        self._update_status_text()

    def stop_stream(self) -> None:
        self.stop_recording()
        self._timer.stop()
        self._engine.stop()
        self.cal_bar.setVisible(False)
        self.start_btn.setText("Start")
        self.cal_btn.setEnabled(False)
        self.rec_btn.setEnabled(False)
        self.stream_combo.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.status_lbl.setText("Stopped.")

    def _toggle_record(self) -> None:
        if self._rec_proc is not None and self._rec_proc.poll() is None:
            self.stop_recording()
        else:
            self.start_recording()

    def _watch_recorder_output(self, proc: subprocess.Popen[str]) -> None:
        if proc.stdout is None:
            return
        try:
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("REC_STATUS "):
                    try:
                        self._rec_stats = json.loads(line[len("REC_STATUS ") :])
                    except Exception:
                        pass
        except Exception:
            pass

    def start_recording(self, out_dir: Path | None = None) -> Path | None:
        """Spawn the separate biofeedback_recorder process to record LSL streams to CSV."""
        if not self._engine.running:
            return None
        if self._rec_proc is not None and self._rec_proc.poll() is None:
            return self._rec_dir

        if not self.lsl_out_cb.isChecked():
            self.lsl_out_cb.setChecked(True)

        if out_dir is None:
            chosen = QFileDialog.getExistingDirectory(
                self, "Select Folder for LSL Recording Session", str(self._last_dir)
            )
            if not chosen:
                return None
            self._last_dir = Path(chosen)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_dir = Path(chosen) / f"biofeedback_rec_{stamp}"
        else:
            session_dir = Path(out_dir)
            self._last_dir = session_dir.parent

        session_dir.mkdir(parents=True, exist_ok=True)
        recorder_script = Path(__file__).resolve().parent.parent / "biofeedback_recorder.py"
        cmd = [
            sys.executable,
            "-u",
            str(recorder_script),
            "--out-dir",
            str(session_dir),
            "--raw-stream",
            self._active_input_stream_name,
            "--stdin-stop",
            "--json-progress",
        ]
        self._rec_stats = {}
        self._rec_dir = session_dir
        self._rec_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._rec_reader_thread = threading.Thread(
            target=self._watch_recorder_output,
            args=(self._rec_proc,),
            name="BiofeedbackRecReader",
            daemon=True,
        )
        self._rec_reader_thread.start()

        self.rec_btn.setText("Stop Rec")
        self.rec_btn.setStyleSheet(
            "background: #dc2626; color: #ffffff; font-weight: 600;"
        )
        self._update_status_text(self._engine.get_feature_render_data()[2])
        return session_dir

    def stop_recording(self) -> None:
        """Stop the separate LSL recorder subprocess cleanly so CSVs and metadata flush."""
        proc = self._rec_proc
        rec_dir = self._rec_dir
        reader = self._rec_reader_thread
        self._rec_proc = None
        self._rec_reader_thread = None

        if proc is not None:
            try:
                if proc.stdin is not None:
                    try:
                        proc.stdin.write("STOP\n")
                        proc.stdin.flush()
                    except Exception:
                        pass
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(timeout=3.0)
            except Exception:
                pass
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.5)

        self._rec_stats = {}
        self.rec_btn.setText("Record")
        self.rec_btn.setStyleSheet("")
        if rec_dir is not None and (rec_dir / "recording_meta.json").is_file():
            self.status_lbl.setText(f"Recording saved to {rec_dir}")

    def start_calibration(self) -> None:
        if not self._engine.running:
            return
        self._engine.start_calibration()
        self.cal_bar.setRange(0, 100)
        self.cal_bar.setValue(0)
        self.cal_bar.setVisible(True)
        self.status_lbl.setText(f"Calibrating templates ({self.cfg.calibration_duration_s:.0f}s)...")

    def _on_ui_tick(self) -> None:
        if not self._engine.running:
            if self._engine.last_error:
                err = self._engine.last_error
                self.stop_stream()
                self.status_lbl.setText(f"Stream error: {err}")
            return
        self._render_ui()

    def process_once(self) -> None:
        """Execute one deterministic processing step and refresh visible UI widgets."""
        if not self._engine.running:
            return
        if self._engine.step_once():
            self._render_ui()

    def _update_status_text(self, t_latest: float = 0.0) -> None:
        if not self._engine.running:
            return
        lsl_state = "ON" if self.lsl_out_cb.isChecked() else "OFF"
        sig_state = "ON" if self.cb_show_signals.isChecked() else "OFF"
        feat_state = "ON" if self.cb_show_features.isChecked() else "OFF"
        msg = (
            f"Streaming @ ~{self._fs:.0f} Hz | t={t_latest:.1f}s | LSL={lsl_state} | "
            f"Signals={sig_state} | Features={feat_state}"
        )
        if self._rec_proc is not None and self._rec_proc.poll() is None:
            st = self._rec_stats
            msg += (
                f" | REC raw={st.get('raw', 0)} filt={st.get('filtered', 0)} ev={st.get('events', 0)}"
            )
        self.status_lbl.setText(msg)

    def _render_ui(self) -> None:
        s, last_ev, t_latest, cal_active, cal_pct, cal_summary, cal_ver, _ = (
            self._engine.get_feature_render_data()
        )

        if cal_active:
            if not self.cal_bar.isVisible():
                self.cal_bar.setVisible(True)
            self.cal_bar.setValue(cal_pct)
        elif self.cal_bar.isVisible():
            self.cal_bar.setVisible(False)

        if cal_ver != self._last_cal_version:
            self._last_cal_version = cal_ver
            self._update_template_plot()
            if cal_summary:
                self.lbl_cal_status.setText(f"Calibrated — {cal_summary}")
                self.status_lbl.setText(f"Calibration complete: {cal_summary}")
                cal_active = False

        show_sig = self.cb_show_signals.isChecked()
        show_feat = self.cb_show_features.isChecked()

        if show_sig or show_feat:
            et, ey, rpeaks, dt, dy, tt, ty, scrs, t_sig = self._engine.get_graph_render_data(
                include_signals=show_sig,
                include_features=show_feat,
            )
            if show_sig:
                self.ecg_curve.setData(et, ey)
                self.eda_curve.setData(dt, dy)

            if show_feat:
                if rpeaks:
                    self.ecg_scatter.setData(x=[p[0] for p in rpeaks], y=[p[1] for p in rpeaks])
                else:
                    self.ecg_scatter.setData(x=[], y=[])

                self.eda_tonic_curve.setData(tt, ty)
                if scrs:
                    self.eda_scatter.setData(x=[p[0] for p in scrs], y=[p[1] for p in scrs])
                else:
                    self.eda_scatter.setData(x=[], y=[])

            if t_sig > 0:
                x0 = max(0.0, t_sig - self.cfg.display_window_s)
                x1 = max(self.cfg.display_window_s, t_sig)
                self.ecg_plot.setXRange(x0, x1, padding=0.01)

        # Control panel KPI metrics always update live regardless of graph toggles
        self.lbl_bpm.setText(f"{s.bpm_rolling:.1f} BPM" if s.bpm_rolling > 0 else "-- BPM")
        self.lbl_tonic.setText(f"{s.eda_tonic:.2f} {self.cfg.eda_unit}")
        self.lbl_phasic.setText(f"{s.eda_phasic:+.2f} {self.cfg.eda_unit}")
        self.lbl_scr_rate.setText(f"{s.eda_scr_rate_per_min:.1f} /min")
        if last_ev is not None:
            self.lbl_last_ev.setText(
                f"Last Event: {last_ev.kind} @ {last_ev.time_s:.2f}s (r={last_ev.confidence:.2f})"
            )

        if not cal_active:
            self._update_status_text(t_latest)

    def _update_template_plot(self) -> None:
        e_tmpl, d_tmpl = self._engine.get_templates()
        if len(e_tmpl) > 1:
            self.tmpl_ecg_curve.setData(np.linspace(0.0, 1.0, len(e_tmpl)), e_tmpl)
        if len(d_tmpl) > 1:
            self.tmpl_eda_curve.setData(np.linspace(0.0, 1.0, len(d_tmpl)), d_tmpl)

    def save_calibration_to_path(self, path: Path) -> Path:
        return self._engine.save_calibration(Path(path))

    def load_calibration_from_path(self, path: Path) -> None:
        cfg = self._engine.load_calibration(Path(path))
        widgets = (
            self.spin_disp_win,
            self.spin_bpm_win,
            self.spin_scr_win,
            self.cb_notch,
            self.spin_eda_m,
            self.spin_eda_b,
            self.edit_eda_unit,
            self.cb_eda_inv,
            self.spin_eda_num,
            self.cb_online_adapt,
            self.spin_qrs_low,
            self.spin_qrs_high,
            self.spin_qrs_order,
            self.spin_tonic_lp,
            self.spin_tonic_order,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            self.spin_disp_win.setValue(cfg.display_window_s)
            self.spin_bpm_win.setValue(cfg.bpm_window_s)
            self.spin_scr_win.setValue(cfg.scr_window_s)
            self.cb_notch.setChecked(cfg.ecg_notch_50hz)
            self.spin_eda_m.setValue(cfg.eda_scale_m)
            self.spin_eda_b.setValue(cfg.eda_offset_b)
            self.edit_eda_unit.setText(cfg.eda_unit)
            self.cb_eda_inv.setChecked(cfg.eda_invert)
            self.spin_eda_num.setValue(cfg.eda_invert_num)
            self.cb_online_adapt.setChecked(cfg.online_adapt)
            self.spin_qrs_low.setValue(cfg.ecg_qrs_low_hz)
            self.spin_qrs_high.setValue(cfg.ecg_qrs_high_hz)
            self.spin_qrs_order.setValue(int(cfg.ecg_qrs_order))
            self.spin_tonic_lp.setValue(cfg.eda_tonic_lp_hz)
            self.spin_tonic_order.setValue(int(cfg.eda_tonic_order))
        finally:
            for w in widgets:
                w.blockSignals(False)

        self.eda_plot.setLabel("left", f"EDA ({cfg.eda_unit})")
        self._last_cal_version = self._engine.calibration_version
        self._update_template_plot()
        self._rebuild_filter_cards()
        self.lbl_cal_status.setText("Loaded saved calibration templates.")

    def _on_save_cal_clicked(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save Calibration", str(self._last_dir / "biofeedback_cal.json"), "JSON (*.json)"
        )
        if not chosen:
            return
        out = self.save_calibration_to_path(Path(chosen))
        self._last_dir = out.parent
        self.status_lbl.setText(f"Saved calibration to {out.name}")

    def _on_load_cal_clicked(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Load Calibration", str(self._last_dir), "JSON (*.json)"
        )
        if not chosen:
            return
        self.load_calibration_from_path(Path(chosen))
        self._last_dir = Path(chosen).parent
        self.status_lbl.setText(f"Loaded calibration from {Path(chosen).name}")

    def closeEvent(self, event: Any) -> None:
        self.stop_stream()
        super().closeEvent(event)
