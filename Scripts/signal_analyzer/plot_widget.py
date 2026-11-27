"""Stacked Raw + Filtered PyQtGraph plot widget with multi-left Y-axes and locked views."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QFrame, QLabel, QSplitter, QVBoxLayout, QWidget

from .dsp import downsample_for_viewport
from .models import ChannelState, EventDetectionResult, Recording

pg.setConfigOptions(antialias=True)

_LEFT_AXIS_WIDTH = 66


def _channel_axis_label(ch: ChannelState) -> str:
    u = ch.unit.strip()
    return f"{ch.display_name} ({u})" if u else ch.display_name


class _OverlayViewBox(pg.ViewBox):
    """ViewBox overlaid on main_vb that lets canvas mouse/wheel events pass through.

    When scrolled/dragged directly on its linked AxisItem (``axis is not None``),
    it zooms/pans that specific axis independently.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)

    def wheelEvent(self, ev: Any, axis: Any = None) -> None:
        if axis is None:
            ev.ignore()
            return
        super().wheelEvent(ev, axis=axis)

    def mouseDragEvent(self, ev: Any, axis: Any = None) -> None:
        if axis is None:
            ev.ignore()
            return
        super().mouseDragEvent(ev, axis=axis)


class SingleSignalPlot(QWidget):
    """Single PyQtGraph plot supporting multiple left Y-axes and overlaid ViewBoxes."""

    cursor_moved = pyqtSignal(float, float)
    custom_y_changed = pyqtSignal(int, float, float)

    def __init__(self, title: str, show_x_label: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._show_x_label = show_x_label
        self._tick_font_size: int = 9
        self._label_font_size: int = 10
        self._has_shared_left: bool = True
        self._shared_left_label: str = "Amplitude (Shared)"
        self._updating_ranges = False
        self._last_main_yrange: tuple[float, float] | None = None

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
        self.pi.getAxis("left").setLabel("Amplitude (Shared)", color="#374151", **self._label_style())
        if show_x_label:
            self.pi.getAxis("bottom").setLabel("Time (s)", color="#374151", **self._label_style())
            self.pi.getAxis("bottom").setHeight(self._calc_bottom_axis_height())
        else:
            self.pi.getAxis("bottom").setStyle(showValues=False)
            self.pi.getAxis("bottom").setHeight(14)

        self.legend = self.pi.addLegend(offset=(10, 10), brush=pg.mkBrush(255, 255, 255, 215), pen=pg.mkPen("#d1d5db"))

        # Custom-scale state: ch_idx -> {"vb": ViewBox, "axis": AxisItem, "slot": Callable}
        self._custom_views: dict[int, dict[str, Any]] = {}
        self._curve_items: list[Any] = []
        self._curve_records: list[tuple[pg.PlotCurveItem, np.ndarray, np.ndarray]] = []
        self._refreshing_curves: bool = False
        self._marker_lines: list[Any] = []
        self._scatter_items: list[Any] = []
        self._layout_signature: tuple[int, ...] | None = None

        self.pi.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.pi.vb.sigYRangeChanged.connect(self._on_main_y_range_changed)
        self.pi.vb.sigXRangeChanged.connect(lambda *_args: self._refresh_downsampled_curves())
        self.pi.vb.sigResized.connect(lambda *_args: self._refresh_downsampled_curves())

    def _on_main_y_range_changed(self, _vb: Any, y_range: Any) -> None:
        new_y0, new_y1 = float(y_range[0]), float(y_range[1])
        if self._updating_ranges:
            self._last_main_yrange = (new_y0, new_y1)
            return
        old = self._last_main_yrange
        self._last_main_yrange = (new_y0, new_y1)
        if old is None or not self._custom_views:
            return
        old_y0, old_y1 = old
        old_span = old_y1 - old_y0
        if old_span <= 1e-12:
            return
        f0 = (new_y0 - old_y0) / old_span
        f1 = (new_y1 - old_y0) / old_span

        self._updating_ranges = True
        try:
            for ch_idx, entry in self._custom_views.items():
                c_vb: pg.ViewBox = entry["vb"]
                c0, c1 = c_vb.viewRange()[1]
                c_span = c1 - c0
                if c_span > 1e-12:
                    nc0 = float(c0 + f0 * c_span)
                    nc1 = float(c0 + f1 * c_span)
                    if nc1 > nc0:
                        c_vb.setYRange(nc0, nc1, padding=0.0)
                        self.custom_y_changed.emit(ch_idx, nc0, nc1)
        finally:
            self._updating_ranges = False

    def _on_custom_vb_y_changed(self, ch_idx: int, c_vb: pg.ViewBox) -> None:
        if self._updating_ranges:
            return
        c0, c1 = c_vb.viewRange()[1]
        self.custom_y_changed.emit(ch_idx, float(c0), float(c1))

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

    def _calc_bottom_axis_height(self) -> int:
        return max(36, int(self._tick_font_size * 1.8 + self._label_font_size * 1.8 + 14))

    def set_axis_font_sizes(self, tick_pt: int, label_pt: int) -> None:
        self._tick_font_size = int(tick_pt)
        self._label_font_size = int(label_pt)
        tick_font = self._tick_qfont()
        lbl_style = self._label_style()
        left_w = self._calc_left_axis_width()

        for axis_name in ("left", "bottom", "top", "right"):
            self.pi.getAxis(axis_name).setStyle(tickFont=tick_font)

        primary_ax = self.pi.getAxis("left")
        primary_ax.setWidth(left_w)
        primary_ax.setLabel(
            self._shared_left_label if self._has_shared_left else "",
            color="#374151",
            **lbl_style,
        )

        if self._show_x_label:
            bottom_ax = self.pi.getAxis("bottom")
            bottom_ax.setHeight(self._calc_bottom_axis_height())
            bottom_ax.setLabel("Time (s)", color="#374151", **lbl_style)

        for entry in self._custom_views.values():
            ax: pg.AxisItem = entry["axis"]
            ax.setStyle(tickFont=tick_font)
            ax.setWidth(left_w)
            ax.setLabel(ax.labelText, color=ax.labelStyle.get("color", "#374151"), **lbl_style)
            entry["slot"]()

    @property
    def main_vb(self) -> pg.ViewBox:
        return self.pi.vb

    def get_custom_vb(self, ch_idx: int) -> pg.ViewBox | None:
        entry = self._custom_views.get(ch_idx)
        return entry["vb"] if entry else None

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

    def _clear_plot_items(self) -> None:
        for item in self._curve_items:
            vb = item.getViewBox()
            if vb is not None:
                vb.removeItem(item)
        self._curve_items.clear()
        self._curve_records.clear()

        for item in self._scatter_items:
            vb = item.getViewBox()
            if vb is not None:
                vb.removeItem(item)
        self._scatter_items.clear()

        for item in self._marker_lines:
            self.pi.removeItem(item)
        self._marker_lines.clear()

        self.legend.clear()

    def _teardown_custom_axes(self) -> None:
        lay = self.pi.layout
        scene = self.pi.scene()
        for entry in self._custom_views.values():
            vb: pg.ViewBox = entry["vb"]
            ax: pg.AxisItem = entry["axis"]
            slot: Callable[[], None] = entry["slot"]
            try:
                self.pi.vb.sigResized.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
            try:
                vb.setYLink(None)
                vb.setXLink(None)
            except Exception:
                pass
            lay.removeItem(ax)
            if scene is not None:
                scene.removeItem(ax)
                scene.removeItem(vb)
        self._custom_views.clear()

    def _rebuild_left_axes(self, custom_channels: list[ChannelState]) -> None:
        """Rebuild left-axis columns in PlotItem.layout so custom axes sit left-to-right."""
        self._teardown_custom_axes()
        lay = self.pi.layout
        scene = self.pi.scene()

        # Detach fixed PlotItem children cleanly before re-placing at shifted columns
        fixed_items = [
            self.pi.titleLabel,
            self.pi.getAxis("top"),
            self.pi.getAxis("bottom"),
            self.pi.getAxis("left"),
            self.pi.vb,
            self.pi.getAxis("right"),
        ]
        for item in fixed_items:
            lay.removeItem(item)

        n_extra = len(custom_channels)
        vb_col = n_extra + 1

        tick_font = self._tick_qfont()
        lbl_style = self._label_style()
        left_w = self._calc_left_axis_width()

        # Custom axes placed sequentially on the left (columns 0 .. n_extra-1)
        for col, ch in enumerate(custom_channels):
            ax = pg.AxisItem("left")
            ax.setWidth(left_w)
            ax.setPen(pg.mkPen(ch.color, width=1.4))
            ax.setTextPen(pg.mkPen(ch.color))
            ax.setStyle(tickFont=tick_font)
            ax.setLabel(_channel_axis_label(ch), color=ch.color, **lbl_style)

            vb = _OverlayViewBox()
            vb.enableAutoRange(x=False, y=False)
            vb.setMouseEnabled(x=False, y=True)
            vb.setXLink(self.pi.vb)
            x0, x1 = self.pi.vb.viewRange()[0]
            if x1 > x0:
                vb.setXRange(x0, x1, padding=0.0)
            scene.addItem(vb)
            ax.linkToView(vb)
            lay.addItem(ax, 2, col)
            vb.sigYRangeChanged.connect(
                lambda _vb, _rng, cid=ch.index, target=vb: self._on_custom_vb_y_changed(cid, target)
            )

            busy_state = {"busy": False}

            def _sync_geometry(
                target_vb: pg.ViewBox = vb,
                state: dict[str, bool] = busy_state,
            ) -> None:
                if state["busy"]:
                    return
                state["busy"] = True
                try:
                    rect = self.pi.vb.sceneBoundingRect()
                    if target_vb.geometry() != rect:
                        target_vb.setGeometry(rect)
                        target_vb.linkedViewChanged(self.pi.vb, target_vb.XAxis)
                finally:
                    state["busy"] = False

            self.pi.vb.sigResized.connect(_sync_geometry)
            _sync_geometry()

            self._custom_views[ch.index] = {
                "vb": vb,
                "axis": ax,
                "slot": _sync_geometry,
            }

        # Primary shared left axis sits right next to the plot area (col = n_extra)
        lay.addItem(self.pi.getAxis("left"), 2, n_extra)
        lay.addItem(self.pi.vb, 2, vb_col)
        lay.addItem(self.pi.titleLabel, 0, vb_col)
        lay.addItem(self.pi.getAxis("top"), 1, vb_col)
        lay.addItem(self.pi.getAxis("bottom"), 3, vb_col)
        lay.addItem(self.pi.getAxis("right"), 2, vb_col + 1)

        self._layout_signature = tuple(ch.index for ch in custom_channels)

    def render(
        self,
        time_s: np.ndarray,
        signals: list[np.ndarray],
        channel_states: list[ChannelState],
        events: list[EventDetectionResult],
        xdf_markers: list[tuple[float, str]],
        shared_y_range: tuple[float, float] | None,
        use_raw_limits: bool = True,
        show_legend: bool = True,
    ) -> None:
        self._clear_plot_items()
        self.legend.setVisible(show_legend)

        visible_channels = [ch for ch in channel_states if ch.visible]
        custom_channels = [ch for ch in visible_channels if ch.custom_y_scale]
        sig_tuple = tuple(ch.index for ch in custom_channels)

        if sig_tuple != self._layout_signature:
            self._rebuild_left_axes(custom_channels)
        else:
            # Update custom axis colors/labels in case user renamed or recolored a channel
            lbl_style = self._label_style()
            for ch in custom_channels:
                entry = self._custom_views.get(ch.index)
                if entry is not None:
                    ax: pg.AxisItem = entry["axis"]
                    ax.setPen(pg.mkPen(ch.color, width=1.4))
                    ax.setTextPen(pg.mkPen(ch.color))
                    ax.setLabel(_channel_axis_label(ch), color=ch.color, **lbl_style)

        # Primary left axis visibility/style depending on whether any visible channel uses shared scale
        shared_channels = [ch for ch in visible_channels if not ch.custom_y_scale]
        has_shared = bool(shared_channels)
        self._has_shared_left = has_shared or not visible_channels
        shared_units = {ch.unit.strip() for ch in shared_channels if ch.unit.strip()}
        if shared_channels and len(shared_units) == 1 and all(ch.unit.strip() for ch in shared_channels):
            self._shared_left_label = f"Amplitude ({next(iter(shared_units))})"
        else:
            self._shared_left_label = "Amplitude (Shared)"

        primary_ax = self.pi.getAxis("left")
        lbl_style = self._label_style()
        if self._has_shared_left:
            primary_ax.setStyle(showValues=True, tickFont=self._tick_qfont())
            primary_ax.setLabel(self._shared_left_label, color="#374151", **lbl_style)
        else:
            primary_ax.setStyle(showValues=False, tickFont=self._tick_qfont())
            primary_ax.setLabel("", color="#374151", **lbl_style)

        # XDF discrete event markers (vertical dashed lines)
        for t_m, lbl in xdf_markers:
            line = pg.InfiniteLine(
                pos=t_m,
                angle=90,
                pen=pg.mkPen("#9ca3af", width=1.0, style=Qt.PenStyle.DashLine),
                label=lbl,
                labelOpts={"position": 0.92, "color": "#4b5563", "rotateAxis": (90, 0)},
            )
            self.pi.addItem(line, ignoreBounds=True)
            self._marker_lines.append(line)

        # Plot visible channel traces (viewport-aware downsampling applied right after)
        for ch in visible_channels:
            y = signals[ch.index]
            pen = pg.mkPen(color=ch.color, width=ch.line_width)
            curve = pg.PlotCurveItem(pen=pen)
            if ch.custom_y_scale and ch.index in self._custom_views:
                target_vb = self._custom_views[ch.index]["vb"]
                target_vb.addItem(curve)
                ymin = ch.raw_y_min if use_raw_limits else ch.filt_y_min
                ymax = ch.raw_y_max if use_raw_limits else ch.filt_y_max
                if ymin is not None and ymax is not None and ymax > ymin:
                    self._updating_ranges = True
                    try:
                        target_vb.setYRange(ymin, ymax, padding=0.0)
                    finally:
                        self._updating_ranges = False
            else:
                self.pi.vb.addItem(curve)

            self.legend.addItem(curve, ch.display_name)
            self._curve_items.append(curve)
            self._curve_records.append((curve, time_s, y))

        # Plot detected event markers (no connecting line)
        legend_seen: set[str] = set()
        for res in events:
            if len(res.times) == 0:
                continue
            ch_state = channel_states[res.channel_index]
            if not ch_state.visible:
                continue
            y_vals = res.raw_values if use_raw_limits else res.processed_values
            scatter = pg.ScatterPlotItem(
                x=res.times,
                y=y_vals,
                symbol=res.marker_symbol,
                size=10,
                pen=pg.mkPen("#111827", width=0.9),
                brush=pg.mkBrush(res.marker_color),
            )
            if ch_state.custom_y_scale and ch_state.index in self._custom_views:
                self._custom_views[ch_state.index]["vb"].addItem(scatter)
            else:
                self.pi.vb.addItem(scatter)
            self._scatter_items.append(scatter)

            lbl = f"{res.event_label} ({ch_state.display_name})"
            if lbl not in legend_seen:
                self.legend.addItem(scatter, lbl)
                legend_seen.add(lbl)

        if shared_y_range is not None:
            ymin, ymax = shared_y_range
            if np.isfinite(ymin) and np.isfinite(ymax) and ymax > ymin:
                self._updating_ranges = True
                try:
                    self.pi.vb.setYRange(ymin, ymax, padding=0.0)
                    self._last_main_yrange = (float(ymin), float(ymax))
                finally:
                    self._updating_ranges = False

        # Sync custom viewbox geometry after layout shifts and refresh downsampled curves
        for entry in self._custom_views.values():
            entry["slot"]()
        self._refresh_downsampled_curves()


class SignalPlotPanel(QWidget):
    """Stacked Raw Signal (top) + Filtered Signal (bottom) display panel."""

    cursor_moved = pyqtSignal(float, float)
    raw_custom_y_changed = pyqtSignal(int, float, float)
    filt_custom_y_changed = pyqtSignal(int, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last_recording: Recording | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.container = QFrame(self)
        self.container.setStyleSheet("QFrame { background-color: #ffffff; }")
        c_layout = QVBoxLayout(self.container)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(4)

        self.splitter = QSplitter(Qt.Orientation.Vertical, self.container)
        self.raw_plot = SingleSignalPlot("Raw Signal", show_x_label=False)
        self.filtered_plot = SingleSignalPlot("Filtered Signal", show_x_label=True)
        self.splitter.addWidget(self.raw_plot)
        self.splitter.addWidget(self.filtered_plot)
        self.splitter.setSizes([320, 320])

        c_layout.addWidget(self.splitter)
        layout.addWidget(self.container)

        # Empty state overlay label
        self.empty_label = QLabel(
            "Import a CSV or XDF recording to begin signal analysis.",
            self.container,
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #6b7280; font-size: 13px; background: transparent;")
        self.empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # Lock X-axis ONLY (Raw and Filtered have independent Y-limits)
        self.filtered_plot.main_vb.setXLink(self.raw_plot.main_vb)

        self.raw_plot.cursor_moved.connect(self.cursor_moved.emit)
        self.filtered_plot.cursor_moved.connect(self.cursor_moved.emit)
        self.raw_plot.custom_y_changed.connect(self.raw_custom_y_changed.emit)
        self.filtered_plot.custom_y_changed.connect(self.filt_custom_y_changed.emit)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.empty_label.setGeometry(self.container.rect())

    def set_axis_font_sizes(self, tick_pt: int, label_pt: int) -> None:
        self.raw_plot.set_axis_font_sizes(tick_pt, label_pt)
        self.filtered_plot.set_axis_font_sizes(tick_pt, label_pt)

    @staticmethod
    def _compute_shared_y_range(
        signals: list[np.ndarray],
        channel_states: list[ChannelState],
        events: list[EventDetectionResult],
        use_raw: bool,
    ) -> tuple[float, float]:
        mins: list[float] = []
        maxs: list[float] = []
        for ch in channel_states:
            if not ch.visible or ch.custom_y_scale:
                continue
            arr = signals[ch.index]
            finite = arr[np.isfinite(arr)]
            if len(finite) > 0:
                mins.append(float(np.min(finite)))
                maxs.append(float(np.max(finite)))
        for res in events:
            ch = channel_states[res.channel_index]
            if ch.visible and not ch.custom_y_scale:
                vals = res.raw_values if use_raw else res.processed_values
                if len(vals) > 0:
                    finite = vals[np.isfinite(vals)]
                    if len(finite) > 0:
                        mins.append(float(np.min(finite)))
                        maxs.append(float(np.max(finite)))

        if not mins or not maxs:
            return (-1.0, 1.0)
        lo = min(mins)
        hi = max(maxs)
        span = hi - lo
        pad = (0.06 * span) if span > 1e-12 else 1.0
        return (lo - pad, hi + pad)

    def update_plots(
        self,
        recording: Recording | None,
        channel_states: list[ChannelState],
        filtered_signals: list[np.ndarray],
        events: list[EventDetectionResult],
        auto_fit: bool = False,
        refit_filtered_y: bool = False,
        show_legend: bool = True,
    ) -> None:
        self._last_recording = recording
        if recording is None:
            self.empty_label.show()
            self.raw_plot.render(np.array([]), [], [], [], [], (-1.0, 1.0), use_raw_limits=True, show_legend=False)
            self.filtered_plot.render(np.array([]), [], [], [], [], (-1.0, 1.0), use_raw_limits=False, show_legend=False)
            return

        self.empty_label.hide()
        raw_signals = [recording.raw_data[:, i] for i in range(recording.n_channels)]

        raw_shared_y = (
            self._compute_shared_y_range(raw_signals, channel_states, events, use_raw=True)
            if auto_fit
            else None
        )
        filt_shared_y = (
            self._compute_shared_y_range(filtered_signals, channel_states, events, use_raw=False)
            if (auto_fit or refit_filtered_y)
            else None
        )

        self.raw_plot.render(
            recording.time_s,
            raw_signals,
            channel_states,
            events,
            recording.markers,
            raw_shared_y,
            use_raw_limits=True,
            show_legend=show_legend,
        )
        self.filtered_plot.render(
            recording.time_s,
            filtered_signals,
            channel_states,
            events,
            recording.markers,
            filt_shared_y,
            use_raw_limits=False,
            show_legend=show_legend,
        )

        if auto_fit and len(recording.time_s) >= 2:
            t_min = float(recording.time_s[0])
            t_max = float(recording.time_s[-1])
            if t_max > t_min:
                self.raw_plot.main_vb.setXRange(t_min, t_max, padding=0.01)
                self.raw_plot._refresh_downsampled_curves()
                self.filtered_plot._refresh_downsampled_curves()

    def refresh_for_dpr(self, dpr: float | None) -> None:
        self.raw_plot._refresh_downsampled_curves(dpr_override=dpr)
        self.filtered_plot._refresh_downsampled_curves(dpr_override=dpr)

    def reset_view(
        self,
        recording: Recording | None,
        channel_states: list[ChannelState],
        filtered_signals: list[np.ndarray],
        events: list[EventDetectionResult],
    ) -> None:
        if recording is None:
            return
        raw_signals = [recording.raw_data[:, i] for i in range(recording.n_channels)]
        raw_y = self._compute_shared_y_range(raw_signals, channel_states, events, use_raw=True)
        filt_y = self._compute_shared_y_range(filtered_signals, channel_states, events, use_raw=False)
        for plot, yr in ((self.raw_plot, raw_y), (self.filtered_plot, filt_y)):
            plot._updating_ranges = True
            try:
                plot.main_vb.setYRange(yr[0], yr[1], padding=0.0)
                plot._last_main_yrange = (float(yr[0]), float(yr[1]))
            finally:
                plot._updating_ranges = False
        if len(recording.time_s) >= 2:
            t_min, t_max = float(recording.time_s[0]), float(recording.time_s[-1])
            if t_max > t_min:
                self.raw_plot.main_vb.setXRange(t_min, t_max, padding=0.01)
                self.raw_plot._refresh_downsampled_curves()
                self.filtered_plot._refresh_downsampled_curves()

    def export_snapshot(self, output_path: Path) -> Path:
        """Render the stacked Raw + Filtered plots as a crisp 2x resolution image."""
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
