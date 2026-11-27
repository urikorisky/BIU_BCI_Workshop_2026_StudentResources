"""Stacked Raw + Filtered Power Spectrum display with overlaid filter response curves."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QFrame, QLabel, QSplitter, QVBoxLayout, QWidget

from .dsp import compute_combined_filter_response, compute_power_spectrum, downsample_for_viewport
from .models import FILTER_DISPLAY_NAMES, ChannelState, FilterSpec, Recording
from .plot_widget import _OverlayViewBox

_LEFT_AXIS_WIDTH = 66
_RIGHT_AXIS_WIDTH = 52
_FILTER_COLORS: list[str] = [
    "#dc2626",  # red
    "#7c3aed",  # violet
    "#059669",  # emerald
    "#d97706",  # amber
    "#db2777",  # pink
    "#0284c7",  # sky
]


def _filter_short_label(idx: int, spec: FilterSpec) -> str:
    name = FILTER_DISPLAY_NAMES.get(spec.filter_type, spec.filter_type)
    p = spec.params
    if spec.filter_type in {"lowpass", "highpass"}:
        return f"{idx + 1}. {name} ({float(p.get('cutoff_hz', 0.0)):.1f} Hz)"
    if spec.filter_type in {"bandpass", "bandstop"}:
        return f"{idx + 1}. {name} ({float(p.get('low_hz', 0.0)):.1f}–{float(p.get('high_hz', 0.0)):.1f} Hz)"
    mode = str(p.get("mode", "lowpass"))
    if mode in {"lowpass", "highpass"}:
        return f"{idx + 1}. {name} {mode[:2].upper()} ({float(p.get('cutoff_hz', 0.0)):.1f} Hz)"
    return f"{idx + 1}. {name} {mode[:2].upper()} ({float(p.get('low_hz', 0.0)):.1f}–{float(p.get('high_hz', 0.0)):.1f} Hz)"


class SingleSpectrumPlot(QWidget):
    """Single power spectrum plot with right-axis overlaid filter gain ViewBox."""

    cursor_moved = pyqtSignal(float, float)

    def __init__(self, title: str, show_x_label: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._show_x_label = show_x_label
        self._tick_font_size: int = 9
        self._label_font_size: int = 10
        self._use_db: bool = True
        self._has_filters: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.plot_widget = pg.PlotWidget(background="w")
        self.plot_widget.setMinimumHeight(180)
        layout.addWidget(self.plot_widget)

        self.pi: pg.PlotItem = self.plot_widget.getPlotItem()
        self.pi.setTitle(title, color="#1f2937", size="11pt")
        self.pi.showGrid(x=True, y=True, alpha=0.20)
        self.pi.setMenuEnabled(False)
        self.pi.hideButtons()
        self.pi.vb.disableAutoRange()

        tick_font = self._tick_qfont()
        for axis_name in ("left", "bottom", "top", "right"):
            ax = self.pi.getAxis(axis_name)
            ax.setPen(pg.mkPen("#4b5563", width=1))
            ax.setTextPen(pg.mkPen("#374151"))
            ax.setStyle(tickFont=tick_font)

        self.pi.getAxis("left").setWidth(self._calc_left_axis_width())
        self.pi.getAxis("left").setLabel("Power (dB)", color="#374151", **self._label_style())
        if show_x_label:
            self.pi.getAxis("bottom").setLabel("Frequency (Hz)", color="#374151", **self._label_style())
            self.pi.getAxis("bottom").setHeight(self._calc_bottom_axis_height())
        else:
            self.pi.getAxis("bottom").setStyle(showValues=False)
            self.pi.getAxis("bottom").setHeight(14)

        # Keep right axis width fixed across both stacked plots so X-axes align
        right_ax = self.pi.getAxis("right")
        right_ax.setWidth(self._calc_right_axis_width())
        right_ax.setLabel("Filter Gain", color="#dc2626", **self._label_style())
        self.pi.showAxis("right", True)

        # Secondary overlaid ViewBox for filter responses [0, 1]
        self.filter_vb = _OverlayViewBox()
        self.filter_vb.setMouseEnabled(x=False, y=False)
        self.filter_vb.setXLink(self.pi.vb)
        self.pi.scene().addItem(self.filter_vb)
        right_ax.linkToView(self.filter_vb)
        self.filter_vb.setYRange(-0.04, 1.08, padding=0.0)

        self._sync_busy = False
        self.pi.vb.sigResized.connect(self._sync_filter_vb)
        self._sync_filter_vb()

        self.legend = self.pi.addLegend(
            offset=(10, 10),
            brush=pg.mkBrush(255, 255, 255, 215),
            pen=pg.mkPen("#d1d5db"),
        )

        self._psd_curves: list[Any] = []
        self._curve_records: list[tuple[pg.PlotCurveItem, np.ndarray, np.ndarray]] = []
        self._refreshing_curves: bool = False
        self._filter_curves: list[Any] = []
        self._cutoff_lines: list[Any] = []

        self.pi.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.pi.vb.sigXRangeChanged.connect(lambda *_args: self._refresh_downsampled_curves())
        self.pi.vb.sigResized.connect(lambda *_args: self._refresh_downsampled_curves())

    def _sync_filter_vb(self) -> None:
        if self._sync_busy:
            return
        self._sync_busy = True
        try:
            rect = self.pi.vb.sceneBoundingRect()
            if self.filter_vb.geometry() != rect:
                self.filter_vb.setGeometry(rect)
                self.filter_vb.linkedViewChanged(self.pi.vb, self.filter_vb.XAxis)
        finally:
            self._sync_busy = False

    def _on_mouse_moved(self, scene_pos: Any) -> None:
        if self.pi.vb.sceneBoundingRect().contains(scene_pos):
            pt = self.pi.vb.mapSceneToView(scene_pos)
            self.cursor_moved.emit(float(pt.x()), float(pt.y()))

    def _tick_qfont(self) -> QFont:
        f = QFont()
        f.setPointSize(self._tick_font_size)
        return f

    def _label_style(self) -> dict[str, str]:
        return {"font-size": f"{self._label_font_size}pt"}

    def _calc_left_axis_width(self) -> int:
        return max(_LEFT_AXIS_WIDTH, int(self._tick_font_size * 4.0 + self._label_font_size * 1.9 + 14))

    def _calc_right_axis_width(self) -> int:
        return max(_RIGHT_AXIS_WIDTH, int(self._tick_font_size * 3.0 + self._label_font_size * 1.8 + 12))

    def _calc_bottom_axis_height(self) -> int:
        return max(36, int(self._tick_font_size * 1.8 + self._label_font_size * 1.8 + 14))

    def set_axis_font_sizes(self, tick_pt: int, label_pt: int) -> None:
        self._tick_font_size = int(tick_pt)
        self._label_font_size = int(label_pt)
        tick_font = self._tick_qfont()
        lbl_style = self._label_style()

        for axis_name in ("left", "bottom", "top", "right"):
            self.pi.getAxis(axis_name).setStyle(tickFont=tick_font)

        left_ax = self.pi.getAxis("left")
        left_ax.setWidth(self._calc_left_axis_width())
        left_ax.setLabel(
            "Power (dB)" if self._use_db else "Power (PSD)",
            color="#374151",
            **lbl_style,
        )

        right_ax = self.pi.getAxis("right")
        right_ax.setWidth(self._calc_right_axis_width())
        right_ax.setLabel(
            "Filter Gain" if self._has_filters else "",
            color="#dc2626",
            **lbl_style,
        )

        if self._show_x_label:
            bottom_ax = self.pi.getAxis("bottom")
            bottom_ax.setHeight(self._calc_bottom_axis_height())
            bottom_ax.setLabel("Frequency (Hz)", color="#374151", **lbl_style)

        self._sync_filter_vb()
        self._refresh_downsampled_curves()

    @property
    def main_vb(self) -> pg.ViewBox:
        return self.pi.vb

    def _viewport_pixel_budget(self, dpr_override: float | None = None) -> tuple[float, float]:
        rect_w = float(self.pi.vb.sceneBoundingRect().width())
        if rect_w <= 1.0:
            rect_w = float(max(self.plot_widget.width(), 800))
        dpr = float(dpr_override) if dpr_override is not None else float(self.devicePixelRatioF() or 1.0)
        return rect_w, max(1.0, dpr)

    def _refresh_downsampled_curves(self, dpr_override: float | None = None) -> None:
        if self._refreshing_curves or not self._curve_records:
            return
        self._refreshing_curves = True
        try:
            x0, x1 = self.pi.vb.viewRange()[0]
            x_min = float(x0) if np.isfinite(x0) else None
            x_max = float(x1) if np.isfinite(x1) else None
            w_px, dpr = self._viewport_pixel_budget(dpr_override)
            for curve, full_x, full_y in self._curve_records:
                dx, dy = downsample_for_viewport(full_x, full_y, x_min, x_max, w_px, dpr)
                curve.setData(dx, dy)
        finally:
            self._refreshing_curves = False

    def _clear_items(self) -> None:
        for item in self._psd_curves:
            self.pi.vb.removeItem(item)
        self._psd_curves.clear()
        self._curve_records.clear()

        for item in self._filter_curves:
            self.filter_vb.removeItem(item)
        self._filter_curves.clear()

        for item in self._cutoff_lines:
            self.filter_vb.removeItem(item)
        self._cutoff_lines.clear()

        self.legend.clear()

    def render(
        self,
        psd_entries: list[tuple[ChannelState, np.ndarray, np.ndarray]],
        fs: float,
        selected_channel: ChannelState | None,
        use_db: bool,
        y_range: tuple[float, float] | None,
        show_legend: bool = True,
    ) -> None:
        self._clear_items()
        self._use_db = use_db
        self.legend.setVisible(show_legend)
        self.pi.getAxis("left").setLabel(
            "Power (dB)" if use_db else "Power (PSD)",
            color="#374151",
            **self._label_style(),
        )

        # 1. Channel PSD curves (viewport-aware downsampling applied right after)
        for ch, freqs, psd in psd_entries:
            if len(freqs) == 0:
                continue
            curve = pg.PlotCurveItem(pen=pg.mkPen(color=ch.color, width=ch.line_width))
            self.pi.vb.addItem(curve)
            self.legend.addItem(curve, ch.display_name)
            self._psd_curves.append(curve)
            self._curve_records.append((curve, freqs, psd))

        # 2. Overlaid filter response lines for the currently selected channel
        has_filters = False
        if selected_channel is not None and fs > 0 and selected_channel.filters:
            f_grid = np.linspace(0.0, fs * 0.5, 512)
            responses, combined = compute_combined_filter_response(
                f_grid, fs, selected_channel.filters
            )
            has_filters = len(responses) > 0

            for idx, (spec, mag) in enumerate(responses):
                color = _FILTER_COLORS[idx % len(_FILTER_COLORS)]
                pen = pg.mkPen(color=color, width=1.8, style=Qt.PenStyle.DashLine)
                f_curve = pg.PlotCurveItem(x=f_grid, y=mag, pen=pen)
                self.filter_vb.addItem(f_curve)
                self.legend.addItem(f_curve, _filter_short_label(idx, spec))
                self._filter_curves.append(f_curve)

                # Vertical cutoff marker line(s)
                p = spec.params
                ftype = spec.filter_type.lower()
                if ftype in {"lowpass", "highpass"} or (
                    ftype in {"step", "knee"} and str(p.get("mode", "lowpass")) in {"lowpass", "highpass"}
                ):
                    cutoffs = [float(p.get("cutoff_hz", 0.0))]
                else:
                    cutoffs = [float(p.get("low_hz", 0.0)), float(p.get("high_hz", 0.0))]

                for fc in cutoffs:
                    vline = pg.InfiniteLine(
                        pos=fc,
                        angle=90,
                        pen=pg.mkPen(color=color, width=1.1, style=Qt.PenStyle.DotLine),
                    )
                    self.filter_vb.addItem(vline)
                    self._cutoff_lines.append(vline)

            if combined is not None:
                c_pen = pg.mkPen(color="#111827", width=2.2, style=Qt.PenStyle.DashDotLine)
                c_curve = pg.PlotCurveItem(x=f_grid, y=combined, pen=c_pen)
                self.filter_vb.addItem(c_curve)
                self.legend.addItem(c_curve, "Combined Filter")
                self._filter_curves.append(c_curve)

        self._has_filters = has_filters
        right_ax = self.pi.getAxis("right")
        right_ax.setStyle(showValues=has_filters, tickFont=self._tick_qfont())
        right_ax.setLabel("Filter Gain" if has_filters else "", color="#dc2626", **self._label_style())
        self.filter_vb.setYRange(-0.04, 1.08, padding=0.0)

        if y_range is not None:
            ymin, ymax = y_range
            if np.isfinite(ymin) and np.isfinite(ymax) and ymax > ymin:
                self.pi.vb.setYRange(ymin, ymax, padding=0.0)

        self._sync_filter_vb()
        self._refresh_downsampled_curves()


class SpectrumPlotPanel(QWidget):
    """Stacked Raw Power Spectrum (top) + Filtered Power Spectrum (bottom) panel."""

    cursor_moved = pyqtSignal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lock_y = True
        self._method = "welch"
        self._use_db = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.container = QFrame(self)
        self.container.setStyleSheet("QFrame { background-color: #ffffff; }")
        c_layout = QVBoxLayout(self.container)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(4)

        self.splitter = QSplitter(Qt.Orientation.Vertical, self.container)
        self.raw_spec = SingleSpectrumPlot("Raw Power Spectrum", show_x_label=False)
        self.filtered_spec = SingleSpectrumPlot("Filtered Power Spectrum", show_x_label=True)
        self.splitter.addWidget(self.raw_spec)
        self.splitter.addWidget(self.filtered_spec)
        self.splitter.setSizes([320, 320])

        c_layout.addWidget(self.splitter)
        layout.addWidget(self.container)

        self.empty_label = QLabel(
            "Import a CSV or XDF recording to view power spectra.",
            self.container,
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #6b7280; font-size: 13px; background: transparent;")
        self.empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.filtered_spec.main_vb.setXLink(self.raw_spec.main_vb)
        self.filtered_spec.main_vb.setYLink(self.raw_spec.main_vb)

        self.raw_spec.cursor_moved.connect(self.cursor_moved.emit)
        self.filtered_spec.cursor_moved.connect(self.cursor_moved.emit)

        self._last_y_range: tuple[float, float] = (-100.0, 20.0)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.empty_label.setGeometry(self.container.rect())

    def set_axis_font_sizes(self, tick_pt: int, label_pt: int) -> None:
        self.raw_spec.set_axis_font_sizes(tick_pt, label_pt)
        self.filtered_spec.set_axis_font_sizes(tick_pt, label_pt)

    def set_y_locked(self, locked: bool) -> None:
        self._lock_y = locked
        if locked:
            self.filtered_spec.main_vb.setYLink(self.raw_spec.main_vb)
        else:
            self.filtered_spec.main_vb.setYLink(None)

    def set_options(self, method: str, use_db: bool) -> None:
        self._method = method.lower()
        self._use_db = bool(use_db)

    @property
    def method(self) -> str:
        return self._method

    @property
    def use_db(self) -> bool:
        return self._use_db

    @staticmethod
    def _compute_psd_y_range(
        raw_entries: list[tuple[ChannelState, np.ndarray, np.ndarray]],
        filt_entries: list[tuple[ChannelState, np.ndarray, np.ndarray]],
        use_db: bool,
    ) -> tuple[float, float]:
        vals: list[np.ndarray] = []
        for _, _, psd in raw_entries + filt_entries:
            finite = psd[np.isfinite(psd)]
            if len(finite) > 0:
                vals.append(finite)
        if not vals:
            return (-80.0, 20.0) if use_db else (0.0, 1.0)
        merged = np.concatenate(vals)
        vmax = float(np.max(merged))
        vmin = float(np.min(merged))
        if use_db:
            # Clamp extreme stopband zero floors (e.g. -200 dB) so signal peak stays clear
            vmin = max(vmin, vmax - 100.0)
        span = vmax - vmin
        pad = (0.06 * span) if span > 1e-12 else 5.0
        return (vmin - pad, vmax + pad)

    def update_spectra(
        self,
        recording: Recording | None,
        channel_states: list[ChannelState],
        filtered_signals: list[np.ndarray],
        selected_idx: int,
        auto_fit: bool = False,
        show_legend: bool = True,
    ) -> None:
        if recording is None:
            self.empty_label.show()
            self.raw_spec.render([], 0.0, None, self._use_db, (-80.0, 20.0), show_legend=False)
            self.filtered_spec.render([], 0.0, None, self._use_db, (-80.0, 20.0), show_legend=False)
            return

        self.empty_label.hide()
        visible = [ch for ch in channel_states if ch.visible]
        raw_entries: list[tuple[ChannelState, np.ndarray, np.ndarray]] = []
        filt_entries: list[tuple[ChannelState, np.ndarray, np.ndarray]] = []

        for ch in visible:
            rf, rp = compute_power_spectrum(
                recording.raw_data[:, ch.index],
                recording.sample_rate,
                method=self._method,
                use_db=self._use_db,
            )
            ff, fp = compute_power_spectrum(
                filtered_signals[ch.index],
                recording.sample_rate,
                method=self._method,
                use_db=self._use_db,
            )
            raw_entries.append((ch, rf, rp))
            filt_entries.append((ch, ff, fp))

        selected_ch = (
            channel_states[selected_idx]
            if 0 <= selected_idx < len(channel_states)
            else None
        )

        y_rng = (
            self._compute_psd_y_range(raw_entries, filt_entries, self._use_db)
            if auto_fit
            else None
        )
        if y_rng is not None:
            self._last_y_range = y_rng

        self.raw_spec.render(
            raw_entries,
            recording.sample_rate,
            selected_ch,
            self._use_db,
            y_rng,
            show_legend=show_legend,
        )
        self.filtered_spec.render(
            filt_entries,
            recording.sample_rate,
            selected_ch,
            self._use_db,
            y_rng if not self._lock_y else None,
            show_legend=show_legend,
        )

        if auto_fit and recording.sample_rate > 0:
            self.raw_spec.main_vb.setXRange(0.0, recording.sample_rate * 0.5, padding=0.01)
            self.raw_spec._refresh_downsampled_curves()
            self.filtered_spec._refresh_downsampled_curves()

    def refresh_for_dpr(self, dpr: float | None) -> None:
        self.raw_spec._refresh_downsampled_curves(dpr_override=dpr)
        self.filtered_spec._refresh_downsampled_curves(dpr_override=dpr)

    def reset_view(
        self,
        recording: Recording | None,
        channel_states: list[ChannelState],
        filtered_signals: list[np.ndarray],
        selected_idx: int,
    ) -> None:
        if recording is None:
            return
        self.update_spectra(
            recording,
            channel_states,
            filtered_signals,
            selected_idx,
            auto_fit=True,
        )

    def export_snapshot(self, output_path: Path) -> Path:
        """Render the stacked Power Spectrum plots as a crisp 2x resolution image."""
        output_path = Path(output_path)
        size = self.container.size()
        scale = 2
        pixmap = QPixmap(max(1, size.width() * scale), max(1, size.height() * scale))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(QColor("#ffffff"))
        self.refresh_for_dpr(float(scale))
        try:
            painter = QPainter(pixmap)
            self.container.render(painter)
            painter.end()
        finally:
            self.refresh_for_dpr(None)
        if not pixmap.save(str(output_path)):
            raise IOError(f"Failed to save snapshot to {output_path}")
        return output_path
