"""Main application window for the Signal Analyzer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QAction, QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .controls_panel import ControlsPanel
from .derived_dialog import DerivedChannelDialog
from .dsp import apply_filter_chain, evaluate_linear_combination
from .events import ROLLING_STAT_LABELS, compute_rolling_event_stat, run_channel_detectors
from .io_loader import (
    export_filtered_csv,
    inspect_xdf_streams,
    load_analysis,
    load_csv,
    load_xdf,
    save_analysis,
)
from .models import (
    DEFAULT_PALETTE,
    ChannelState,
    EventDetectionResult,
    Recording,
    build_default_channel_states,
)
from .plot_widget import SignalPlotPanel
from .spectrum_widget import SpectrumPlotPanel
from .stats_table import EventStatsTable


class StreamPickerDialog(QDialog):
    """Modal dialog to choose a numeric stream when an XDF file contains multiple."""

    def __init__(self, streams_meta: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select XDF Signal Stream")
        self.resize(480, 260)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("This XDF file contains multiple numeric streams. Choose one to load:"))

        self.list_widget = QListWidget()
        for meta in streams_meta:
            txt = (
                f"[{meta['index']}] {meta['name']}  |  type={meta['type']}  |  "
                f"{meta['n_channels']} ch  |  {meta['effective_srate']:.1f} Hz  |  "
                f"{meta['duration_s']:.1f} s ({meta['n_samples']} samples)"
            )
            item = QListWidgetItem(txt)
            item.setData(Qt.ItemDataRole.UserRole, meta["index"])
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        lay.addWidget(self.list_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def selected_stream_index(self) -> int | None:
        item = self.list_widget.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None


class SignalAnalyzerWindow(QMainWindow):
    """Full-featured offline biosignal analysis window."""

    def __init__(self, initial_path: Path | str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("BCI Workshop — Signal Analyzer")
        self.resize(1380, 860)

        self._recording: Recording | None = None
        self._channels: list[ChannelState] = []
        self._filtered_signals: list[np.ndarray] = []
        self._filter_errors: dict[int, dict[str, str]] = {}
        self._events: list[EventDetectionResult] = []
        self._advanced_mode: bool = False
        self._display_view: str = "time"
        self._last_dir: Path = Path.cwd()

        self._build_ui()
        self._apply_mode(False)

        if initial_path is not None:
            p = Path(initial_path)
            if p.is_file():
                if p.name.endswith(".json"):
                    self.load_analysis_from_path(p)
                else:
                    self.load_recording_from_path(p)

    def _build_ui(self) -> None:
        # Top toolbar
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setStyleSheet(
            "QToolBar { background: #f3f4f6; border-bottom: 1px solid #d1d5db; spacing: 6px; padding: 4px 8px; }"
            "QToolButton { padding: 4px 8px; border-radius: 4px; color: #111827; }"
            "QToolButton:hover { background: #e5e7eb; }"
        )
        self.addToolBar(toolbar)

        self.act_import = QAction(icons.open_icon(), "Import", self)
        self.act_import.setToolTip("Import CSV (default) or XDF recording")
        self.act_import.triggered.connect(self._on_import_clicked)
        toolbar.addAction(self.act_import)

        toolbar.addSeparator()

        self.act_save_analysis = QAction(icons.save_icon(), "Save Analysis", self)
        self.act_save_analysis.setToolTip("Save current filters, names, scales, and detectors")
        self.act_save_analysis.triggered.connect(self._on_save_analysis_clicked)
        toolbar.addAction(self.act_save_analysis)

        self.act_load_analysis = QAction(icons.load_icon(), "Load Analysis", self)
        self.act_load_analysis.setToolTip("Load a saved .bci.json analysis")
        self.act_load_analysis.triggered.connect(self._on_load_analysis_clicked)
        toolbar.addAction(self.act_load_analysis)

        toolbar.addSeparator()

        self.act_reset_view = QAction(icons.reset_icon(), "Reset View", self)
        self.act_reset_view.setToolTip("Reset zoom to fit all visible signals")
        self.act_reset_view.triggered.connect(self._on_reset_view_clicked)
        toolbar.addAction(self.act_reset_view)

        self.act_snapshot = QAction(icons.camera_icon(), "Save Snapshot", self)
        self.act_snapshot.setToolTip("Save as image")
        self.act_snapshot.triggered.connect(self._on_snapshot_clicked)
        toolbar.addAction(self.act_snapshot)

        self.act_export_csv = QAction(icons.export_icon(), "Export CSV", self)
        self.act_export_csv.setToolTip("Export filtered signals to CSV")
        self.act_export_csv.triggered.connect(self._on_export_csv_clicked)
        toolbar.addAction(self.act_export_csv)

        # Spacer pushing Basic | Advanced toggle to top-right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        mode_container = QWidget()
        mode_lay = QHBoxLayout(mode_container)
        mode_lay.setContentsMargins(0, 0, 4, 0)
        mode_lay.setSpacing(0)

        self.btn_basic = QPushButton("Basic")
        self.btn_advanced = QPushButton("Advanced")
        for btn, left_r, right_r in ((self.btn_basic, "4px", "0px"), (self.btn_advanced, "0px", "4px")):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ padding: 4px 12px; border: 1px solid #9ca3af; "
                f"border-top-left-radius: {left_r}; border-bottom-left-radius: {left_r}; "
                f"border-top-right-radius: {right_r}; border-bottom-right-radius: {right_r}; "
                f"background: #ffffff; color: #374151; font-weight: 500; }}"
                f"QPushButton:checked {{ background: #2563eb; color: #ffffff; border-color: #2563eb; font-weight: 600; }}"
            )

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.btn_basic, 0)
        self.mode_group.addButton(self.btn_advanced, 1)
        self.btn_basic.setChecked(True)
        self.btn_basic.toggled.connect(self._on_mode_toggled)
        mode_lay.addWidget(self.btn_basic)
        mode_lay.addWidget(self.btn_advanced)
        toolbar.addWidget(mode_container)

        # Central layout
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(self.main_splitter)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical, self.main_splitter)

        # Left-top container: 3-way display selector + display_splitter
        display_area = QWidget(self.left_splitter)
        display_lay = QVBoxLayout(display_area)
        display_lay.setContentsMargins(0, 0, 0, 0)
        display_lay.setSpacing(4)

        view_bar = QHBoxLayout()
        view_bar.setContentsMargins(4, 4, 4, 0)
        view_bar.setSpacing(0)
        view_lbl = QLabel("View: ")
        view_lbl.setStyleSheet("color: #374151; font-weight: 600; margin-right: 6px;")
        view_bar.addWidget(view_lbl)

        self.btn_view_time = QPushButton("Time Domain")
        self.btn_view_spectrum = QPushButton("Power Spectrum")
        self.btn_view_both = QPushButton("Side-by-Side")
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        for idx, (btn, mode_key, lr, rr) in enumerate([
            (self.btn_view_time, "time", "4px", "0px"),
            (self.btn_view_spectrum, "spectrum", "0px", "0px"),
            (self.btn_view_both, "both", "0px", "4px"),
        ]):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("view_mode", mode_key)
            btn.setStyleSheet(
                f"QPushButton {{ padding: 3px 10px; border: 1px solid #9ca3af; "
                f"border-top-left-radius: {lr}; border-bottom-left-radius: {lr}; "
                f"border-top-right-radius: {rr}; border-bottom-right-radius: {rr}; "
                f"background: #ffffff; color: #374151; font-size: 12px; font-weight: 500; }}"
                f"QPushButton:checked {{ background: #1f2937; color: #ffffff; border-color: #1f2937; font-weight: 600; }}"
            )
            self.view_group.addButton(btn, idx)
            btn.clicked.connect(lambda _checked=False, k=mode_key: self.set_display_view(k))
            view_bar.addWidget(btn)
        view_bar.addStretch(1)
        display_lay.addLayout(view_bar)

        self.display_splitter = QSplitter(Qt.Orientation.Horizontal, display_area)
        self.plot_panel = SignalPlotPanel(self.display_splitter)
        self.spectrum_panel = SpectrumPlotPanel(self.display_splitter)
        self.display_splitter.addWidget(self.plot_panel)
        self.display_splitter.addWidget(self.spectrum_panel)
        self.display_splitter.setSizes([640, 640])
        display_lay.addWidget(self.display_splitter, 1)

        self.stats_table = EventStatsTable(self.left_splitter)
        self.left_splitter.addWidget(display_area)
        self.left_splitter.addWidget(self.stats_table)
        self.left_splitter.setStretchFactor(0, 4)
        self.left_splitter.setStretchFactor(1, 1)
        self.left_splitter.setSizes([640, 170])

        self.controls_panel = ControlsPanel(self.main_splitter)
        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(self.controls_panel)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([980, 360])

        self.set_display_view("time")

        # Wire signals
        self.controls_panel.channel_selected.connect(self._on_channel_selected)
        self.controls_panel.channel_display_changed.connect(self._on_display_changed)
        self.controls_panel.channel_filters_changed.connect(self._on_channel_filters_changed)
        self.controls_panel.channel_detectors_changed.connect(self._on_channel_detectors_changed)
        self.controls_panel.autoscale_channel_requested.connect(self.autoscale_channel)
        self.controls_panel.revert_scale_requested.connect(self.revert_channel_scale)
        self.controls_panel.show_all_requested.connect(self._on_show_all_requested)
        self.controls_panel.y_lock_changed.connect(self._on_y_lock_changed)
        self.controls_panel.spectrum_options_changed.connect(self._on_spectrum_options_changed)
        self.controls_panel.axis_fonts_changed.connect(self._on_axis_fonts_changed)
        self.controls_panel.create_derived_requested.connect(self._on_create_derived_clicked)
        self.controls_panel.edit_derived_requested.connect(self._on_edit_derived_clicked)
        self.controls_panel.delete_derived_requested.connect(self._on_delete_derived_clicked)

        self.plot_panel.cursor_moved.connect(self._on_cursor_moved)
        self.plot_panel.raw_custom_y_changed.connect(self._on_raw_custom_y_changed)
        self.plot_panel.filt_custom_y_changed.connect(self._on_filt_custom_y_changed)
        self.spectrum_panel.cursor_moved.connect(self._on_spectrum_cursor_moved)
        self.stats_table.rolling_stat_requested.connect(self.add_rolling_stat_channel)

        self.status_file_lbl = QLabel("No recording loaded")
        self.status_cursor_lbl = QLabel("")
        self.statusBar().addWidget(self.status_file_lbl, 1)
        self.statusBar().addPermanentWidget(self.status_cursor_lbl)

        self._update_toolbar_enabled()

    def _update_toolbar_enabled(self) -> None:
        has_rec = self._recording is not None
        self.act_save_analysis.setEnabled(has_rec)
        self.act_reset_view.setEnabled(has_rec)
        self.act_snapshot.setEnabled(has_rec)
        self.act_export_csv.setEnabled(has_rec)

    def _on_mode_toggled(self, basic_checked: bool) -> None:
        self._apply_mode(not basic_checked)

    def _apply_mode(self, advanced: bool) -> None:
        self._advanced_mode = advanced
        self.btn_advanced.setChecked(advanced)
        self.btn_basic.setChecked(not advanced)
        self.controls_panel.set_advanced_mode(advanced)
        self.stats_table.set_advanced_mode(advanced)
        self.status_cursor_lbl.setVisible(advanced)

    def set_ui_mode(self, mode: str) -> None:
        self._apply_mode(mode.lower() == "advanced")

    def _on_cursor_moved(self, x: float, y: float) -> None:
        if self._advanced_mode and self._recording is not None:
            self.status_cursor_lbl.setText(f"t = {x:.3f} s, y = {y:.3f}")

    def _on_spectrum_cursor_moved(self, freq: float, pwr: float) -> None:
        if self._advanced_mode and self._recording is not None:
            unit = "dB" if self.spectrum_panel.use_db else "PSD"
            self.status_cursor_lbl.setText(f"f = {freq:.2f} Hz, {unit} = {pwr:.2f}")

    def set_display_view(self, view: str) -> None:
        view = view.lower()
        if view not in {"time", "spectrum", "both"}:
            view = "time"
        self._display_view = view
        self.btn_view_time.setChecked(view == "time")
        self.btn_view_spectrum.setChecked(view == "spectrum")
        self.btn_view_both.setChecked(view == "both")
        self.plot_panel.setVisible(view in {"time", "both"})
        self.spectrum_panel.setVisible(view in {"spectrum", "both"})
        if view == "both":
            w = max(self.display_splitter.width(), 800)
            self.display_splitter.setSizes([w // 2, w // 2])

    def _on_y_lock_changed(self, locked: bool) -> None:
        self.spectrum_panel.set_y_locked(locked)

    def _on_spectrum_options_changed(self, method: str, use_db: bool) -> None:
        self.spectrum_panel.set_options(method, use_db)
        self._render_spectrum(auto_fit=True)

    def _on_axis_fonts_changed(self, tick_pt: int, label_pt: int) -> None:
        self.plot_panel.set_axis_font_sizes(tick_pt, label_pt)
        self.spectrum_panel.set_axis_font_sizes(tick_pt, label_pt)

    def _on_channel_selected(self, _idx: int) -> None:
        # Update overlaid filter lines on the spectrum plot for the newly selected channel
        self._render_spectrum(auto_fit=False)

    # ------------------------------------------------------------------
    # Data Loading & Persistence
    # ------------------------------------------------------------------

    def _on_import_clicked(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Import Recording",
            str(self._last_dir),
            "CSV files (*.csv);;XDF files (*.xdf);;All supported (*.csv *.xdf);;All files (*.*)",
        )
        if not chosen:
            return
        path = Path(chosen)
        self._last_dir = path.parent
        self.load_recording_from_path(path)

    def load_recording_from_path(
        self,
        path: Path,
        stream_index: int | None = None,
    ) -> bool:
        path = Path(path)
        try:
            suffix = path.suffix.lower()
            if suffix == ".xdf":
                if stream_index is None:
                    streams_meta = inspect_xdf_streams(path)
                    if not streams_meta:
                        raise ValueError("No numeric signal streams found in this XDF file.")
                    if len(streams_meta) > 1:
                        dlg = StreamPickerDialog(streams_meta, self)
                        if dlg.exec() != QDialog.DialogCode.Accepted:
                            return False
                        stream_index = dlg.selected_stream_index()
                    else:
                        stream_index = streams_meta[0]["index"]
                rec = load_xdf(path, stream_index=stream_index)
            else:
                rec = load_csv(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", f"Could not load {path.name}:\n{exc}")
            return False

        self._recording = rec
        self._channels = build_default_channel_states(rec)
        self._recompute_all()
        self.controls_panel.set_channels(self._channels, rec.sample_rate, self._filter_errors, selected_idx=0)
        self._render_all(auto_fit=True)
        self._update_status_bar()
        self._update_toolbar_enabled()
        return True

    def _on_save_analysis_clicked(self) -> None:
        if self._recording is None:
            return
        default_name = (
            self._recording.source_path.with_suffix(".bci.json")
            if self._recording.source_path
            else self._last_dir / "analysis.bci.json"
        )
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Save Analysis",
            str(default_name),
            "BCI Analysis (*.bci.json);;JSON files (*.json)",
        )
        if not chosen:
            return
        view_state = {
            "y_locked": self.controls_panel.lock_y_cb.isChecked(),
            "display_view": self._display_view,
            "spectrum_method": self.spectrum_panel.method,
            "spectrum_db": self.spectrum_panel.use_db,
            "axis_tick_font_size": self.controls_panel.tick_font_spin.value(),
            "axis_label_font_size": self.controls_panel.label_font_spin.value(),
            "x_range": list(self.plot_panel.raw_plot.main_vb.viewRange()[0]),
            "raw_y_range": list(self.plot_panel.raw_plot.main_vb.viewRange()[1]),
            "filt_y_range": list(self.plot_panel.filtered_plot.main_vb.viewRange()[1]),
        }
        try:
            out = save_analysis(
                Path(chosen),
                self._recording,
                self._channels,
                "advanced" if self._advanced_mode else "basic",
                view_state,
            )
            self._last_dir = out.parent
            self.statusBar().showMessage(f"Saved analysis to {out.name}", 4000)
        except Exception as exc:
            QMessageBox.critical(self, "Save Analysis Failed", str(exc))

    def _on_load_analysis_clicked(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Load Analysis",
            str(self._last_dir),
            "BCI Analysis (*.bci.json *.json);;All files (*.*)",
        )
        if not chosen:
            return
        self.load_analysis_from_path(Path(chosen))

    def load_analysis_from_path(self, analysis_path: Path) -> bool:
        try:
            state = load_analysis(analysis_path)
        except Exception as exc:
            QMessageBox.critical(self, "Load Analysis Failed", f"Invalid analysis file:\n{exc}")
            return False

        src = Path(state.source_path) if state.source_path else None
        if src is None or not src.is_file():
            located, _ = QFileDialog.getOpenFileName(
                self,
                "Locate Recording File for Analysis",
                str(analysis_path.parent),
                "CSV files (*.csv);;XDF files (*.xdf);;All files (*.*)",
            )
            if not located:
                return False
            src = Path(located)

        ok = self.load_recording_from_path(src, stream_index=state.stream_index)
        if not ok or self._recording is None:
            return False

        # Restore base channel states matching loaded channel count
        base_n = len(self._channels)
        by_idx = {ch.index: ch for ch in state.channels}
        for i in range(base_n):
            if i in by_idx and by_idx[i].derived_spec is None:
                self._channels[i] = by_idx[i]

        self.set_ui_mode(state.ui_mode)
        y_locked = bool(state.view_state.get("y_locked", True))
        self.controls_panel.lock_y_cb.setChecked(y_locked)
        self._on_y_lock_changed(y_locked)

        spec_method = str(state.view_state.get("spectrum_method", "welch"))
        spec_db = bool(state.view_state.get("spectrum_db", True))
        self.spectrum_panel.set_options(spec_method, spec_db)
        self.controls_panel.set_spectrum_options(spec_method, spec_db)

        tick_pt = int(state.view_state.get("axis_tick_font_size", 9))
        label_pt = int(state.view_state.get("axis_label_font_size", 10))
        self.controls_panel.set_axis_font_sizes(tick_pt, label_pt)
        self._on_axis_fonts_changed(tick_pt, label_pt)

        self.set_display_view(str(state.view_state.get("display_view", "time")))

        self._recompute_all()

        # Reconstruct saved derived channels in two passes:
        # Pass 1: linear_combination channels (in index order so chained combinations resolve)
        idx_map: dict[int, int] = {i: i for i in range(base_n)}
        linear_saved = sorted(
            [
                c
                for c in state.channels
                if c.derived_spec is not None
                and c.derived_spec.get("kind") == "linear_combination"
                and c.index >= base_n
            ],
            key=lambda c: c.index,
        )
        for saved_ch in linear_saved:
            old_idx = saved_ch.index
            spec = dict(saved_ch.derived_spec or {})
            remapped_terms = []
            for t in spec.get("terms", []):
                t_copy = dict(t)
                old_src = int(t_copy.get("channel_index", 0))
                t_copy["channel_index"] = idx_map.get(old_src, old_src)
                remapped_terms.append(t_copy)
            spec["terms"] = remapped_terms
            saved_ch.derived_spec = spec

            sig = evaluate_linear_combination(
                self._recording.raw_data, self._filtered_signals, spec
            )
            new_idx = len(self._channels)
            idx_map[old_idx] = new_idx
            self._recording.raw_data = np.hstack([self._recording.raw_data, sig[:, np.newaxis]])
            self._recording.channel_names.append(saved_ch.original_name)
            saved_ch.index = new_idx
            self._channels.append(saved_ch)
            filtered, errs = apply_filter_chain(sig, self._recording.sample_rate, saved_ch.filters)
            self._filtered_signals.append(filtered)
            self._filter_errors[new_idx] = errs

        if linear_saved:
            self._recompute_events()

        # Pass 2: rolling_event_stat channels
        rolling_saved = sorted(
            [
                c
                for c in state.channels
                if c.derived_spec is not None
                and c.derived_spec.get("kind") == "rolling_event_stat"
                and c.index >= base_n
            ],
            key=lambda c: c.index,
        )
        for saved_ch in rolling_saved:
            old_idx = saved_ch.index
            spec = dict(saved_ch.derived_spec or {})
            old_src = int(spec.get("source_channel_index", 0))
            src_idx = idx_map.get(old_src, old_src)
            spec["source_channel_index"] = src_idx
            saved_ch.derived_spec = spec

            det_type = str(spec.get("detector_type", "qrs"))
            stat_key = str(spec.get("stat_key", "frequency_hz"))
            win_ms = float(spec.get("window_ms", 5000.0))
            step_ms = float(spec.get("step_ms", 500.0))
            match = next(
                (e for e in self._events if e.channel_index == src_idx and e.detector_type == det_type),
                None,
            )
            if match is None:
                sig = np.zeros(self._recording.n_samples, dtype=float)
            else:
                sig = compute_rolling_event_stat(
                    self._recording.time_s,
                    match,
                    stat_key,
                    window_s=win_ms / 1000.0,
                    step_s=step_ms / 1000.0,
                )
            new_idx = len(self._channels)
            idx_map[old_idx] = new_idx
            self._recording.raw_data = np.hstack([self._recording.raw_data, sig[:, np.newaxis]])
            self._recording.channel_names.append(saved_ch.original_name)
            saved_ch.index = new_idx
            self._channels.append(saved_ch)
            filtered, errs = apply_filter_chain(sig, self._recording.sample_rate, saved_ch.filters)
            self._filtered_signals.append(filtered)
            self._filter_errors[new_idx] = errs

        if rolling_saved:
            self._recompute_events()

        self.controls_panel.set_channels(
            self._channels, self._recording.sample_rate, self._filter_errors, selected_idx=0
        )
        self._render_all(auto_fit=True)
        self._update_status_bar()

        x_rng = state.view_state.get("x_range")
        raw_y_rng = state.view_state.get("raw_y_range", state.view_state.get("y_range"))
        filt_y_rng = state.view_state.get("filt_y_range", state.view_state.get("y_range"))
        if isinstance(x_rng, list) and len(x_rng) == 2:
            self.plot_panel.raw_plot.main_vb.setXRange(float(x_rng[0]), float(x_rng[1]), padding=0.0)
        if isinstance(raw_y_rng, list) and len(raw_y_rng) == 2:
            self.plot_panel.raw_plot.main_vb.setYRange(float(raw_y_rng[0]), float(raw_y_rng[1]), padding=0.0)
        if isinstance(filt_y_rng, list) and len(filt_y_rng) == 2:
            self.plot_panel.filtered_plot.main_vb.setYRange(float(filt_y_rng[0]), float(filt_y_rng[1]), padding=0.0)

        self._last_dir = analysis_path.parent
        self.statusBar().showMessage(f"Loaded analysis from {analysis_path.name}", 4000)
        return True

    # ------------------------------------------------------------------
    # Signal Computation & Display Updates
    # ------------------------------------------------------------------

    def _recompute_channel(self, ch_idx: int) -> None:
        if self._recording is None:
            return
        raw = self._recording.raw_data[:, ch_idx]
        ch = self._channels[ch_idx]
        filtered, errs = apply_filter_chain(raw, self._recording.sample_rate, ch.filters)
        self._filtered_signals[ch_idx] = filtered
        self._filter_errors[ch_idx] = errs
        self._refresh_linear_derived_channels()
        self._recompute_events()

    def _recompute_all(self) -> None:
        if self._recording is None:
            self._filtered_signals = []
            self._filter_errors = {}
            self._events = []
            return
        self._filtered_signals = []
        self._filter_errors = {}
        for ch in self._channels:
            raw = self._recording.raw_data[:, ch.index]
            filtered, errs = apply_filter_chain(raw, self._recording.sample_rate, ch.filters)
            self._filtered_signals.append(filtered)
            self._filter_errors[ch.index] = errs
        self._refresh_linear_derived_channels()
        self._recompute_events()

    def _refresh_linear_derived_channels(self) -> None:
        """Re-evaluate linear-combination derived channels in order so downstream channels stay synced."""
        if self._recording is None:
            return
        for ch in self._channels:
            spec = ch.derived_spec
            if not spec or spec.get("kind") != "linear_combination":
                continue
            sig = evaluate_linear_combination(
                self._recording.raw_data, self._filtered_signals, spec
            )
            self._recording.raw_data[:, ch.index] = sig
            filtered, errs = apply_filter_chain(sig, self._recording.sample_rate, ch.filters)
            self._filtered_signals[ch.index] = filtered
            self._filter_errors[ch.index] = errs

    def _dependent_channel_indices(self, target_idx: int) -> set[int]:
        """Return all channel indices that directly or transitively depend on target_idx."""
        dependents: set[int] = set()
        frontier = {target_idx}
        while frontier:
            cur = frontier.pop()
            for ch in self._channels:
                if ch.index == target_idx or ch.index in dependents or not ch.derived_spec:
                    continue
                kind = ch.derived_spec.get("kind")
                refs: set[int] = set()
                if kind == "linear_combination":
                    refs = {int(t.get("channel_index", -1)) for t in ch.derived_spec.get("terms", [])}
                elif kind == "rolling_event_stat":
                    refs = {int(ch.derived_spec.get("source_channel_index", -1))}
                if cur in refs:
                    dependents.add(ch.index)
                    frontier.add(ch.index)
        return dependents

    def _on_create_derived_clicked(self) -> None:
        if self._recording is None or not self._channels:
            return
        sel_idx = max(0, self.controls_panel.ch_combo.currentIndex())
        dlg = DerivedChannelDialog(self._channels, initial_channel_idx=sel_idx, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, unit, spec = dlg.result_config()
            self.add_linear_combination_channel(name, unit, spec)

    def _on_edit_derived_clicked(self, ch_idx: int) -> None:
        if self._recording is None or not (0 <= ch_idx < len(self._channels)):
            return
        ch = self._channels[ch_idx]
        if not ch.derived_spec or ch.derived_spec.get("kind") != "linear_combination":
            return
        excluded = {ch_idx} | self._dependent_channel_indices(ch_idx)
        dlg = DerivedChannelDialog(
            self._channels,
            initial_channel_idx=ch_idx,
            existing=ch,
            excluded_indices=excluded,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, unit, spec = dlg.result_config()
            self.update_linear_combination_channel(ch_idx, name, unit, spec)

    def _on_delete_derived_clicked(self, ch_idx: int) -> None:
        self.delete_derived_channel(ch_idx)

    def add_linear_combination_channel(
        self,
        name: str,
        unit: str,
        spec: dict[str, Any],
    ) -> int | None:
        """Create a new derived channel from a linear combination spec and arbitrary unit."""
        if self._recording is None:
            return None
        sig = evaluate_linear_combination(
            self._recording.raw_data, self._filtered_signals, spec
        )
        new_idx = len(self._channels)
        self._recording.raw_data = np.hstack([self._recording.raw_data, sig[:, np.newaxis]])
        self._recording.channel_names.append(name)

        new_ch = ChannelState(
            index=new_idx,
            original_name=name,
            display_name=name,
            unit=unit.strip(),
            visible=True,
            color=DEFAULT_PALETTE[new_idx % len(DEFAULT_PALETTE)],
            line_width=1.6,
            derived_spec=dict(spec),
        )
        self._channels.append(new_ch)
        self._filtered_signals.append(sig.copy())
        self._filter_errors[new_idx] = {}

        self.controls_panel.set_channels(
            self._channels,
            self._recording.sample_rate,
            self._filter_errors,
            selected_idx=new_idx,
        )
        self.autoscale_channel(new_idx)
        self._update_status_bar()
        self.statusBar().showMessage(f"Added derived channel: {name}", 4000)
        return new_idx

    def update_linear_combination_channel(
        self,
        ch_idx: int,
        name: str,
        unit: str,
        spec: dict[str, Any],
    ) -> None:
        """Update an existing linear-combination derived channel's formula, name, and unit."""
        if self._recording is None or not (0 <= ch_idx < len(self._channels)):
            return
        ch = self._channels[ch_idx]
        ch.display_name = name.strip() or ch.original_name
        ch.unit = unit.strip()
        ch.derived_spec = dict(spec)
        self._recompute_all()
        self.controls_panel.set_channels(
            self._channels,
            self._recording.sample_rate,
            self._filter_errors,
            selected_idx=ch_idx,
        )
        if ch.custom_y_scale:
            self.autoscale_channel(ch_idx)
        else:
            self._render_all(auto_fit=False)
        self._update_status_bar()
        self.statusBar().showMessage(f"Updated derived channel: {ch.display_name}", 4000)

    def delete_derived_channel(self, ch_idx: int) -> bool:
        """Delete a derived channel if no other derived channel depends on it."""
        if self._recording is None or not (0 <= ch_idx < len(self._channels)):
            return False
        ch = self._channels[ch_idx]
        if ch.derived_spec is None:
            return False

        deps = sorted(self._dependent_channel_indices(ch_idx))
        if deps:
            dep_names = ", ".join(self._channels[i].display_name for i in deps)
            QMessageBox.warning(
                self,
                "Cannot Delete Channel",
                f"'{ch.display_name}' cannot be deleted because these derived channel(s) depend on it:\n{dep_names}",
            )
            return False

        removed_name = ch.display_name
        self._recording.raw_data = np.delete(self._recording.raw_data, ch_idx, axis=1)
        self._recording.channel_names.pop(ch_idx)
        self._channels.pop(ch_idx)
        self._filtered_signals.pop(ch_idx)

        for new_i, c in enumerate(self._channels):
            c.index = new_i
            if c.derived_spec:
                kind = c.derived_spec.get("kind")
                if kind == "linear_combination":
                    for term in c.derived_spec.get("terms", []):
                        src = int(term.get("channel_index", 0))
                        if src > ch_idx:
                            term["channel_index"] = src - 1
                elif kind == "rolling_event_stat":
                    src = int(c.derived_spec.get("source_channel_index", 0))
                    if src > ch_idx:
                        c.derived_spec["source_channel_index"] = src - 1

        self._recompute_all()
        next_sel = min(ch_idx, len(self._channels) - 1)
        self.controls_panel.set_channels(
            self._channels,
            self._recording.sample_rate,
            self._filter_errors,
            selected_idx=next_sel,
        )
        self._render_all(auto_fit=False)
        self._update_status_bar()
        self.statusBar().showMessage(f"Deleted derived channel: {removed_name}", 4000)
        return True

    def _recompute_events(self) -> None:
        if self._recording is None:
            self._events = []
            return
        all_events: list[EventDetectionResult] = []
        for ch in self._channels:
            raw = self._recording.raw_data[:, ch.index]
            proc = self._filtered_signals[ch.index]
            all_events.extend(
                run_channel_detectors(ch, self._recording.time_s, self._recording.sample_rate, raw, proc)
            )
        self._events = all_events
        self._refresh_derived_channels()

    def _refresh_derived_channels(self) -> None:
        """Keep derived rolling-stat channels synced when source filters/detectors change."""
        if self._recording is None:
            return
        for ch in self._channels:
            spec = ch.derived_spec
            if not spec or spec.get("kind") != "rolling_event_stat":
                continue
            src_idx = int(spec.get("source_channel_index", -1))
            det_type = str(spec.get("detector_type", ""))
            stat_key = str(spec.get("stat_key", "frequency_hz"))
            win_ms = float(spec.get("window_ms", 5000.0))
            step_ms = float(spec.get("step_ms", 500.0))
            match = next(
                (e for e in self._events if e.channel_index == src_idx and e.detector_type == det_type),
                None,
            )
            if match is None:
                sig = np.zeros(self._recording.n_samples, dtype=float)
            else:
                sig = compute_rolling_event_stat(
                    self._recording.time_s,
                    match,
                    stat_key,
                    window_s=win_ms / 1000.0,
                    step_s=step_ms / 1000.0,
                )
            self._recording.raw_data[:, ch.index] = sig
            filtered, errs = apply_filter_chain(sig, self._recording.sample_rate, ch.filters)
            self._filtered_signals[ch.index] = filtered
            self._filter_errors[ch.index] = errs

    def add_rolling_stat_channel(
        self,
        res: EventDetectionResult,
        stat_key: str,
        window_ms: float = 5000.0,
        step_ms: float = 500.0,
    ) -> int | None:
        """Create a new derived channel plotting a rolling event statistic over time."""
        if self._recording is None:
            return None

        sig = compute_rolling_event_stat(
            self._recording.time_s,
            res,
            stat_key,
            window_s=window_ms / 1000.0,
            step_s=step_ms / 1000.0,
        )
        new_idx = len(self._channels)
        stat_lbl = ROLLING_STAT_LABELS.get(stat_key, stat_key)
        ch_name = f"{res.event_label} ({res.channel_name}) — {stat_lbl} ({window_ms:.0f}ms)"

        self._recording.raw_data = np.hstack([self._recording.raw_data, sig[:, np.newaxis]])
        self._recording.channel_names.append(ch_name)

        new_ch = ChannelState(
            index=new_idx,
            original_name=ch_name,
            display_name=ch_name,
            visible=True,
            color=DEFAULT_PALETTE[new_idx % len(DEFAULT_PALETTE)],
            line_width=1.6,
            derived_spec={
                "kind": "rolling_event_stat",
                "source_channel_index": int(res.channel_index),
                "detector_type": str(res.detector_type),
                "stat_key": str(stat_key),
                "window_ms": float(window_ms),
                "step_ms": float(step_ms),
            },
        )
        self._channels.append(new_ch)
        self._filtered_signals.append(sig.copy())
        self._filter_errors[new_idx] = {}

        self.controls_panel.set_channels(
            self._channels,
            self._recording.sample_rate,
            self._filter_errors,
            selected_idx=new_idx,
        )
        # Detach to its own Y-axis so the statistic isn't squashed on the raw signal's scale
        self.autoscale_channel(new_idx)
        self._update_status_bar()
        self.statusBar().showMessage(f"Added channel: {ch_name}", 4000)
        return new_idx

    def _render_spectrum(self, auto_fit: bool = False) -> None:
        sel_idx = self.controls_panel.ch_combo.currentIndex()
        if sel_idx < 0:
            sel_idx = 0
        self.spectrum_panel.update_spectra(
            self._recording,
            self._channels,
            self._filtered_signals,
            selected_idx=sel_idx,
            auto_fit=auto_fit,
        )

    def _render_all(self, auto_fit: bool = False, refit_filtered_y: bool = False) -> None:
        self.plot_panel.update_plots(
            self._recording,
            self._channels,
            self._filtered_signals,
            self._events,
            auto_fit=auto_fit,
            refit_filtered_y=refit_filtered_y,
        )
        self._render_spectrum(auto_fit=auto_fit)
        self.stats_table.update_events(self._events)

    def _on_display_changed(self) -> None:
        self._recompute_events()  # ensures channel_name stays synced in stats table
        self._render_all(auto_fit=False)

    def _on_channel_filters_changed(self, ch_idx: int) -> None:
        self._recompute_channel(ch_idx)
        self.controls_panel.update_filter_errors(self._filter_errors)
        ch = self._channels[ch_idx] if 0 <= ch_idx < len(self._channels) else None
        self._render_all(auto_fit=False, refit_filtered_y=bool(ch and not ch.custom_y_scale))

    def _on_channel_detectors_changed(self, ch_idx: int) -> None:
        self._recompute_events()
        self._render_all(auto_fit=False)

    @staticmethod
    def _signal_bounds(arr: np.ndarray) -> tuple[float, float]:
        finite = arr[np.isfinite(arr)]
        if len(finite) == 0:
            return (-1.0, 1.0)
        vmin, vmax = float(np.min(finite)), float(np.max(finite))
        span = vmax - vmin
        pad = (0.06 * span) if span > 1e-12 else 1.0
        return (vmin - pad, vmax + pad)

    def _on_raw_custom_y_changed(self, ch_idx: int, lo: float, hi: float) -> None:
        if 0 <= ch_idx < len(self._channels):
            self._channels[ch_idx].raw_y_min = lo
            self._channels[ch_idx].raw_y_max = hi
            if self.controls_panel.ch_combo.currentIndex() == ch_idx:
                self.controls_panel.refresh_scale_ui()

    def _on_filt_custom_y_changed(self, ch_idx: int, lo: float, hi: float) -> None:
        if 0 <= ch_idx < len(self._channels):
            self._channels[ch_idx].filt_y_min = lo
            self._channels[ch_idx].filt_y_max = hi
            if self.controls_panel.ch_combo.currentIndex() == ch_idx:
                self.controls_panel.refresh_scale_ui()

    def autoscale_channel(self, ch_idx: int) -> None:
        if self._recording is None or not (0 <= ch_idx < len(self._channels)):
            return
        ch = self._channels[ch_idx]
        r_lo, r_hi = self._signal_bounds(self._recording.raw_data[:, ch_idx])
        f_lo, f_hi = self._signal_bounds(self._filtered_signals[ch_idx])
        ch.custom_y_scale = True
        ch.raw_y_min = r_lo
        ch.raw_y_max = r_hi
        ch.filt_y_min = f_lo
        ch.filt_y_max = f_hi
        self.controls_panel.refresh_scale_ui()
        self._render_all(auto_fit=False)

    def revert_channel_scale(self, ch_idx: int) -> None:
        if not (0 <= ch_idx < len(self._channels)):
            return
        ch = self._channels[ch_idx]
        ch.custom_y_scale = False
        ch.raw_y_min = None
        ch.raw_y_max = None
        ch.filt_y_min = None
        ch.filt_y_max = None
        self.controls_panel.refresh_scale_ui()
        self._render_all(auto_fit=True)

    def _on_show_all_requested(self, visible: bool) -> None:
        for ch in self._channels:
            ch.visible = visible
        self.controls_panel.set_channels(
            self._channels,
            self._recording.sample_rate if self._recording else 250.0,
            self._filter_errors,
            selected_idx=self.controls_panel.ch_combo.currentIndex(),
        )
        self._render_all(auto_fit=True)

    def _on_reset_view_clicked(self) -> None:
        self.plot_panel.reset_view(
            self._recording, self._channels, self._filtered_signals, self._events
        )
        sel_idx = max(0, self.controls_panel.ch_combo.currentIndex())
        self.spectrum_panel.reset_view(
            self._recording, self._channels, self._filtered_signals, sel_idx
        )

    def _on_snapshot_clicked(self) -> None:
        if self._recording is None:
            return
        stem = self._recording.source_path.stem if self._recording.source_path else "snapshot"
        default_img = self._last_dir / f"{stem}_snapshot.png"
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Save Snapshot as Image",
            str(default_img),
            "PNG Image (*.png);;JPEG Image (*.jpg *.jpeg);;All files (*.*)",
        )
        if not chosen:
            return
        try:
            out = self.export_display_snapshot(Path(chosen))
            self._last_dir = out.parent
            self.statusBar().showMessage(f"Saved snapshot to {out.name}", 4000)
        except Exception as exc:
            QMessageBox.critical(self, "Snapshot Failed", str(exc))

    def export_display_snapshot(self, output_path: Path) -> Path:
        """Render the currently visible plot area (Time, Spectrum, or Side-by-Side) at 2x DPI."""
        output_path = Path(output_path)
        size = self.display_splitter.size()
        scale = 2
        pixmap = QPixmap(max(1, size.width() * scale), max(1, size.height() * scale))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(QColor("#ffffff"))
        self.plot_panel.refresh_for_dpr(float(scale))
        self.spectrum_panel.refresh_for_dpr(float(scale))
        try:
            painter = QPainter(pixmap)
            self.display_splitter.render(painter)
            painter.end()
        finally:
            self.plot_panel.refresh_for_dpr(None)
            self.spectrum_panel.refresh_for_dpr(None)
        if not pixmap.save(str(output_path)):
            raise IOError(f"Failed to save snapshot to {output_path}")
        return output_path

    def _on_export_csv_clicked(self) -> None:
        if self._recording is None:
            return
        stem = self._recording.source_path.stem if self._recording.source_path else "signal"
        default_csv = self._last_dir / f"{stem}_filtered.csv"
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Export Filtered Signals to CSV",
            str(default_csv),
            "CSV files (*.csv);;All files (*.*)",
        )
        if not chosen:
            return
        try:
            out = export_filtered_csv(
                self._recording, self._channels, self._filtered_signals, Path(chosen)
            )
            self._last_dir = out.parent
            QMessageBox.information(
                self,
                "CSV Export Complete",
                f"Exported {len(self._channels)} channel(s) ({self._recording.n_samples} samples) to:\n{out}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "CSV Export Failed", str(exc))

    def _update_status_bar(self) -> None:
        rec = self._recording
        if rec is None:
            self.status_file_lbl.setText("No recording loaded")
            return
        fname = rec.source_path.name if rec.source_path else rec.stream_name
        vis_count = sum(1 for c in self._channels if c.visible)
        extra = ""
        if vis_count < rec.n_channels:
            extra = f" (showing {vis_count} of {rec.n_channels})"
        self.status_file_lbl.setText(
            f"{fname}  |  Stream: {rec.stream_name}  |  "
            f"{rec.n_channels} ch{extra}  |  {rec.sample_rate:.1f} Hz  |  "
            f"{rec.duration_s:.2f} s ({rec.n_samples} samples)"
        )
