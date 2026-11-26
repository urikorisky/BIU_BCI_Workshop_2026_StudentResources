"""
Interactive Vector ADC Quantization Visualizer (1 to 15 bits, 0–5 V)

Visualizes:
  - Continuous-like random 0–5 V analog signal (blue)
  - Quantized signal for 1–15 bits (red)
  - Quantization error filled between the two curves (light red)
  - Unified Y-axis showing Voltage (V), Bin index (0 .. 2^N - 1), and Binary code
  - Live Average |Error| and Sum |Error| (in Volts) for the visible time window
  - Vector rendering on screen (QPainter), smooth zoom/pan, and SVG / PNG snapshots
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Tuple

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def generate_random_signal(
    seed: int,
    duration_s: float = 1.0,
    n_samples: int = 1000,
    dynamic_range: float = 4.80,
    mean_v: float = 2.50,
) -> Tuple[np.ndarray, np.ndarray]:
    """Generate a smooth random 0–5 V signal with configurable dynamic range and mean."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0.0, duration_s, n_samples)

    y = np.zeros_like(t)
    n_components = 10
    freqs = rng.uniform(0.4, 7.5, size=n_components)
    phases = rng.uniform(0.0, 2.0 * np.pi, size=n_components)
    # Weight lower frequencies higher for a realistic sensor/biosignal feel
    amps = rng.uniform(0.3, 1.0, size=n_components) / (freqs ** 0.55)

    for f, p, a in zip(freqs, phases, amps):
        y += a * np.sin(2.0 * np.pi * f * t + p)

    y_span = float(np.max(y) - np.min(y))
    if y_span < 1e-9:
        y = np.full_like(t, np.clip(mean_v, 0.0, 5.0))
    else:
        y_zero_mean = (y - float(np.mean(y))) / y_span
        y = np.clip(mean_v + dynamic_range * y_zero_mean, 0.0, 5.0)
    return t, y


def quantize_signal(
    v: np.ndarray, bits: int, v_ref: float = 5.0
) -> Tuple[np.ndarray, np.ndarray]:
    """Quantize voltage array into 2^bits bins across [0, v_ref]."""
    n_bins = 1 << bits
    max_bin = n_bins - 1
    bins = np.clip(np.round((v / v_ref) * max_bin), 0, max_bin).astype(np.int64)
    v_q = (bins / max_bin) * v_ref
    return v_q, bins


class QuantizationCanvas(QWidget):
    """Pure-vector QPainter canvas with zoom, pan, unified Y-ticks, and SVG/PNG export."""

    V_MIN = 0.0
    V_MAX = 5.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(760, 480)
        self.setMouseTracking(True)

        self.bits: int = 6
        self.seed: int = 42
        self.duration_s: float = 1.0
        self.dynamic_range: float = 4.80
        self.mean_v: float = 2.50
        self.t, self.v_orig = generate_random_signal(
            self.seed, self.duration_s, dynamic_range=self.dynamic_range, mean_v=self.mean_v
        )
        self.v_quant, self.bins = quantize_signal(self.v_orig, self.bits, self.V_MAX)

        # Current viewport in data coordinates
        self.x_min: float = 0.0
        self.x_max: float = self.duration_s
        self.y_min: float = -0.15
        self.y_max: float = 5.15

        # Panning state
        self._panning: bool = False
        self._pan_start_px: QPointF | None = None
        self._pan_start_limits: Tuple[float, float, float, float] | None = None

        # Callback to notify parent window of error stat updates
        self.on_stats_changed = None

    def set_bits(self, bits: int) -> None:
        self.bits = max(1, min(15, bits))
        self.v_quant, self.bins = quantize_signal(self.v_orig, self.bits, self.V_MAX)
        self.update()
        self._emit_stats()

    def _regenerate_signal(self) -> None:
        self.t, self.v_orig = generate_random_signal(
            self.seed, self.duration_s, dynamic_range=self.dynamic_range, mean_v=self.mean_v
        )
        self.v_quant, self.bins = quantize_signal(self.v_orig, self.bits, self.V_MAX)
        self.update()
        self._emit_stats()

    def set_seed(self, seed: int) -> None:
        self.seed = seed
        self._regenerate_signal()

    def set_signal_params(self, dynamic_range: float, mean_v: float) -> None:
        self.dynamic_range = dynamic_range
        self.mean_v = mean_v
        self._regenerate_signal()

    def reset_view(self) -> None:
        self.x_min = 0.0
        self.x_max = self.duration_s
        self.y_min = -0.15
        self.y_max = 5.15
        self.update()
        self._emit_stats()

    def zoom_by(self, factor: float, cx_frac: float = 0.5, cy_frac: float = 0.5,
                zoom_x: bool = True, zoom_y: bool = True) -> None:
        if zoom_x:
            x_span = self.x_max - self.x_min
            new_x_span = max(0.002, min(self.duration_s * 2.0, x_span * factor))
            cx = self.x_min + cx_frac * x_span
            self.x_min = cx - cx_frac * new_x_span
            self.x_max = self.x_min + new_x_span

        if zoom_y:
            y_span = self.y_max - self.y_min
            new_y_span = max(0.001, min(10.0, y_span * factor))
            cy = self.y_max - cy_frac * y_span
            self.y_max = cy + cy_frac * new_y_span
            self.y_min = self.y_max - new_y_span

        self.update()
        self._emit_stats()

    def compute_visible_stats(self) -> dict:
        mask = (self.t >= self.x_min) & (self.t <= self.x_max)
        if not np.any(mask):
            return {
                "count": 0,
                "avg_abs": 0.0,
                "sum_abs": 0.0,
                "avg_signed": 0.0,
                "avg_abs_bins": 0.0,
                "sum_abs_bins": 0.0,
                "avg_signed_bins": 0.0,
            }
        err = self.v_quant[mask] - self.v_orig[mask]
        abs_err = np.abs(err)
        max_bin = (1 << self.bits) - 1
        scale = max_bin / self.V_MAX
        return {
            "count": int(np.sum(mask)),
            "avg_abs": float(np.mean(abs_err)),
            "sum_abs": float(np.sum(abs_err)),
            "avg_signed": float(np.mean(err)),
            "avg_abs_bins": float(np.mean(abs_err) * scale),
            "sum_abs_bins": float(np.sum(abs_err) * scale),
            "avg_signed_bins": float(np.mean(err) * scale),
        }

    def _emit_stats(self) -> None:
        if self.on_stats_changed is not None:
            self.on_stats_changed(self.compute_visible_stats())

    def _plot_rect(self, width: float, height: float) -> QRectF:
        # Reserve enough left margin for "5.00 V | 32767 | 111111111111111"
        left = max(210.0, 145.0 + self.bits * 7.5)
        right = width - 28.0
        top = 42.0
        bottom = height - 54.0
        return QRectF(left, top, max(100.0, right - left), max(100.0, bottom - top))

    def _data_to_px(self, x: float, y: float, rect: QRectF) -> QPointF:
        px = rect.left() + ((x - self.x_min) / (self.x_max - self.x_min)) * rect.width()
        py = rect.bottom() - ((y - self.y_min) / (self.y_max - self.y_min)) * rect.height()
        return QPointF(px, py)

    def _y_ticks(self) -> list[Tuple[float, int, str]]:
        """Return <= 11 equidistant nice-voltage Y ticks (e.g. 0.5V, 0.25V) with Bin & Binary."""
        n_bins = 1 << self.bits
        max_bin = n_bins - 1
        dec_width = len(str(max_bin))

        vis_lo = max(self.V_MIN, self.y_min)
        vis_hi = min(self.V_MAX, self.y_max)
        if vis_hi <= vis_lo:
            return []

        span_v = vis_hi - vis_lo
        bin_step_v = self.V_MAX / max_bin

        # If zoomed in so close that <= 10 individual bins span the view, show exact bins
        b_lo = max(0, int(math.ceil((vis_lo / self.V_MAX) * max_bin)))
        b_hi = min(max_bin, int(math.floor((vis_hi / self.V_MAX) * max_bin)))
        if b_hi >= b_lo and (b_hi - b_lo + 1) <= 10 and span_v <= 10.0 * bin_step_v:
            ticks = []
            for b in range(b_lo, b_hi + 1):
                v = (b / max_bin) * self.V_MAX
                label = f"{v:5.3f} V | {b:>{dec_width}} | {b:0{self.bits}b}"
                ticks.append((v, b, label))
            return ticks

        # Otherwise choose the smallest clean voltage step giving <= 11 equidistant ticks
        # (At default 0..5 V view, step = 0.50 V -> 0.00, 0.50, ..., 5.00 V)
        nice_steps = []
        for exp in range(0, -5, -1):
            base = 10.0 ** exp
            for mult in (5.0, 2.5, 1.0):
                nice_steps.append(mult * base)
        nice_steps.sort()

        v_step = 0.5
        for s in nice_steps:
            if span_v / s <= 10.5:
                v_step = s
                break

        decimals = 2 if v_step >= 0.05 else (3 if v_step >= 0.005 else 4)
        first_idx = math.ceil((vis_lo - 1e-9) / v_step)
        last_idx = math.floor((vis_hi + 1e-9) / v_step)

        ticks = []
        for idx in range(first_idx, last_idx + 1):
            v = round(idx * v_step, 6)
            if v < self.V_MIN - 1e-9 or v > self.V_MAX + 1e-9:
                continue
            v = min(self.V_MAX, max(self.V_MIN, v))
            b = int(np.clip(round((v / self.V_MAX) * max_bin), 0, max_bin))
            label = f"{v:>{decimals + 2}.{decimals}f} V | {b:>{dec_width}} | {b:0{self.bits}b}"
            ticks.append((v, b, label))
        return ticks

    def _x_ticks(self) -> list[Tuple[float, str]]:
        span = self.x_max - self.x_min
        if span <= 0:
            return []
        raw_step = span / 8.0
        mag = 10.0 ** math.floor(math.log10(raw_step))
        for mult in (1.0, 2.0, 2.5, 5.0, 10.0):
            step = mult * mag
            if span / step <= 10.0:
                break
        first = math.ceil(self.x_min / step) * step
        ticks = []
        x = first
        while x <= self.x_max + 1e-12:
            if abs(x) < 1e-12:
                x = 0.0
            decimals = max(0, -int(math.floor(math.log10(step))) + (1 if step * 10 % 10 != 0 else 0))
            decimals = min(4, decimals)
            ticks.append((x, f"{x:.{decimals}f} s"))
            x += step
        return ticks

    def _visible_indices(self) -> slice:
        i0 = max(0, int(np.searchsorted(self.t, self.x_min, side="left")) - 1)
        i1 = min(len(self.t), int(np.searchsorted(self.t, self.x_max, side="right")) + 1)
        return slice(i0, i1)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self.render_to_painter(painter, self.width(), self.height())
        painter.end()

    def render_to_painter(self, painter: QPainter, width: float, height: float) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Background
        painter.fillRect(QRectF(0, 0, width, height), QColor("#FFFFFF"))
        rect = self._plot_rect(width, height)

        # Plot area background
        painter.fillRect(rect, QColor("#FAFCFF"))

        y_ticks = self._y_ticks()
        x_ticks = self._x_ticks()

        # Grid lines
        grid_pen = QPen(QColor("#E4E9F2"), 1.0)
        painter.setPen(grid_pen)
        for v, _, _ in y_ticks:
            py = self._data_to_px(self.x_min, v, rect).y()
            painter.drawLine(QPointF(rect.left(), py), QPointF(rect.right(), py))
        for xt, _ in x_ticks:
            px = self._data_to_px(xt, self.y_min, rect).x()
            painter.drawLine(QPointF(px, rect.top()), QPointF(px, rect.bottom()))

        # Clip to plot rect for curves
        painter.save()
        painter.setClipRect(rect)

        sl = self._visible_indices()
        t_vis = self.t[sl]
        v_vis = self.v_orig[sl]
        q_vis = self.v_quant[sl]

        if len(t_vis) >= 2:
            # 1. Light red fill between original signal and quantized staircase
            poly = QPolygonF()
            for ti, vi in zip(t_vis, v_vis):
                poly.append(self._data_to_px(float(ti), float(vi), rect))
            poly.append(self._data_to_px(float(t_vis[-1]), float(q_vis[-1]), rect))
            for idx in range(len(t_vis) - 1, 0, -1):
                poly.append(self._data_to_px(float(t_vis[idx]), float(q_vis[idx - 1]), rect))
                poly.append(self._data_to_px(float(t_vis[idx - 1]), float(q_vis[idx - 1]), rect))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(235, 65, 65, 60))
            painter.drawPolygon(poly)

            # 2. Original continuous-like signal in blue
            blue_path = QPainterPath()
            p0 = self._data_to_px(float(t_vis[0]), float(v_vis[0]), rect)
            blue_path.moveTo(p0)
            for ti, vi in zip(t_vis[1:], v_vis[1:]):
                blue_path.lineTo(self._data_to_px(float(ti), float(vi), rect))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#1D63DC"), 2.0))
            painter.drawPath(blue_path)

            # 3. Quantized signal in red (step staircase)
            red_path = QPainterPath()
            q0 = self._data_to_px(float(t_vis[0]), float(q_vis[0]), rect)
            red_path.moveTo(q0)
            for idx in range(1, len(t_vis)):
                # Horizontal hold from t[idx-1] to t[idx] at q[idx-1], then vertical step
                red_path.lineTo(self._data_to_px(float(t_vis[idx]), float(q_vis[idx - 1]), rect))
                red_path.lineTo(self._data_to_px(float(t_vis[idx]), float(q_vis[idx]), rect))
            painter.setPen(QPen(QColor("#D92B2B"), 1.8))
            painter.drawPath(red_path)

        painter.restore()

        # Plot border
        painter.setPen(QPen(QColor("#334155"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        # Y-axis header & ticks
        mono_font = QFont("Consolas", 9)
        if not mono_font.exactMatch():
            mono_font = QFont("Courier New", 9)
        painter.setFont(mono_font)
        fm = QFontMetrics(mono_font)

        header_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(header_font)
        painter.setPen(QColor("#334155"))
        painter.drawText(
            QRectF(8, 10, rect.left() - 16, 24),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "Voltage | Bin | Binary",
        )

        painter.setFont(mono_font)
        for v, _, label in y_ticks:
            py = self._data_to_px(self.x_min, v, rect).y()
            painter.setPen(QPen(QColor("#334155"), 1.2))
            painter.drawLine(QPointF(rect.left() - 6, py), QPointF(rect.left(), py))
            painter.setPen(QColor("#1E293B"))
            painter.drawText(
                QRectF(6, py - fm.height() / 2 - 1, rect.left() - 14, fm.height() + 2),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        # X-axis ticks & label
        axis_font = QFont("Segoe UI", 9)
        painter.setFont(axis_font)
        for xt, xlabel in x_ticks:
            px = self._data_to_px(xt, self.y_min, rect).x()
            painter.setPen(QPen(QColor("#334155"), 1.2))
            painter.drawLine(QPointF(px, rect.bottom()), QPointF(px, rect.bottom() + 6))
            painter.setPen(QColor("#1E293B"))
            painter.drawText(
                QRectF(px - 45, rect.bottom() + 8, 90, 20),
                Qt.AlignmentFlag.AlignCenter,
                xlabel,
            )

        title_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.drawText(
            QRectF(rect.left(), rect.bottom() + 28, rect.width(), 22),
            Qt.AlignmentFlag.AlignCenter,
            "Time (seconds)",
        )

        # Top-right legend & title inside header strip
        stats = self.compute_visible_stats()
        n_bins = 1 << self.bits
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.setPen(QColor("#0F172A"))
        painter.drawText(
            QRectF(rect.left(), 8, rect.width(), 26),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{self.bits}-Bit ADC Quantization ({n_bins:,} bins)  |  Seed: {self.seed}  |  "
            f"Avg |Err|: {stats['avg_abs']:.5f} V ({stats['avg_abs_bins']:.3f} bins)  |  "
            f"Sum |Err|: {stats['sum_abs']:.3f} V ({stats['sum_abs_bins']:.1f} bins)",
        )

        # Legend box in top-right of plot
        leg_w, leg_h = 205, 66
        leg_rect = QRectF(rect.right() - leg_w - 10, rect.top() + 10, leg_w, leg_h)
        painter.setPen(QPen(QColor("#CBD5E1"), 1.0))
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawRoundedRect(leg_rect, 6, 6)

        painter.setFont(QFont("Segoe UI", 8))
        # Blue line
        y_l = leg_rect.top() + 15
        painter.setPen(QPen(QColor("#1D63DC"), 2.2))
        painter.drawLine(QPointF(leg_rect.left() + 10, y_l), QPointF(leg_rect.left() + 34, y_l))
        painter.setPen(QColor("#1E293B"))
        painter.drawText(QPointF(leg_rect.left() + 42, y_l + 4), "Original Signal (0–5 V)")

        # Red line
        y_l += 18
        painter.setPen(QPen(QColor("#D92B2B"), 2.0))
        painter.drawLine(QPointF(leg_rect.left() + 10, y_l), QPointF(leg_rect.left() + 34, y_l))
        painter.setPen(QColor("#1E293B"))
        painter.drawText(QPointF(leg_rect.left() + 42, y_l + 4), f"Quantized ({self.bits}-bit)")

        # Light red fill
        y_l += 18
        painter.setPen(QPen(QColor(235, 65, 65, 140), 1.0))
        painter.setBrush(QColor(235, 65, 65, 65))
        painter.drawRect(QRectF(leg_rect.left() + 10, y_l - 6, 24, 11))
        painter.setPen(QColor("#1E293B"))
        painter.drawText(QPointF(leg_rect.left() + 42, y_l + 4), "Quantization Error")

    # ------------------------------------------------------------------
    # Mouse interaction: Wheel Zoom & Drag Pan
    # ------------------------------------------------------------------
    def wheelEvent(self, event: QWheelEvent) -> None:
        rect = self._plot_rect(self.width(), self.height())
        pos = event.position()
        cx_frac = max(0.0, min(1.0, (pos.x() - rect.left()) / rect.width()))
        cy_frac = max(0.0, min(1.0, (pos.y() - rect.top()) / rect.height()))

        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 0.82 if delta > 0 else 1.22

        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            zoom_x, zoom_y = False, True
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            zoom_x, zoom_y = True, False
        elif pos.x() < rect.left() and rect.top() <= pos.y() <= rect.bottom():
            # Scrolling over the Y-axis labels zooms Y only
            zoom_x, zoom_y = False, True
        elif pos.y() > rect.bottom() and rect.left() <= pos.x() <= rect.right():
            # Scrolling over the X-axis labels zooms X only
            zoom_x, zoom_y = True, False
        else:
            zoom_x, zoom_y = True, True

        self.zoom_by(factor, cx_frac, cy_frac, zoom_x=zoom_x, zoom_y=zoom_y)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._panning = True
            self._pan_start_px = event.position()
            self._pan_start_limits = (self.x_min, self.x_max, self.y_min, self.y_max)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._pan_start_px is not None and self._pan_start_limits is not None:
            rect = self._plot_rect(self.width(), self.height())
            dx_px = event.position().x() - self._pan_start_px.x()
            dy_px = event.position().y() - self._pan_start_px.y()
            x0, x1, y0, y1 = self._pan_start_limits
            dx_data = (dx_px / rect.width()) * (x1 - x0)
            dy_data = (dy_px / rect.height()) * (y1 - y0)
            self.x_min = x0 - dx_data
            self.x_max = x1 - dx_data
            self.y_min = y0 + dy_data
            self.y_max = y1 + dy_data
            self.update()
            self._emit_stats()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------
    # Vector SVG & High-Res PNG Export
    # ------------------------------------------------------------------
    def export_png(self, path: str, scale: float = 2.0) -> None:
        w = int(self.width() * scale)
        h = int(self.height() * scale)
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor("#FFFFFF"))
        painter = QPainter(img)
        painter.scale(scale, scale)
        self.render_to_painter(painter, float(self.width()), float(self.height()))
        painter.end()
        img.save(path, "PNG")

    def export_svg(self, path: str) -> None:
        """Write a pure-vector SVG matching the exact current view."""
        w = float(max(900, self.width()))
        h = float(max(560, self.height()))
        rect = self._plot_rect(w, h)
        y_ticks = self._y_ticks()
        x_ticks = self._x_ticks()
        stats = self.compute_visible_stats()
        n_bins = 1 << self.bits

        sl = self._visible_indices()
        t_vis = self.t[sl]
        v_vis = self.v_orig[sl]
        q_vis = self.v_quant[sl]

        lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" '
            f'width="{w:.0f}" height="{h:.0f}">',
            f'  <rect width="{w:.0f}" height="{h:.0f}" fill="#FFFFFF"/>',
            f'  <defs><clipPath id="plot-clip"><rect x="{rect.left():.2f}" y="{rect.top():.2f}" '
            f'width="{rect.width():.2f}" height="{rect.height():.2f}"/></clipPath></defs>',
            f'  <rect x="{rect.left():.2f}" y="{rect.top():.2f}" width="{rect.width():.2f}" '
            f'height="{rect.height():.2f}" fill="#FAFCFF"/>',
        ]

        # Grid
        for v, _, _ in y_ticks:
            py = self._data_to_px(self.x_min, v, rect).y()
            lines.append(
                f'  <line x1="{rect.left():.2f}" y1="{py:.2f}" x2="{rect.right():.2f}" y2="{py:.2f}" '
                f'stroke="#E4E9F2" stroke-width="1"/>'
            )
        for xt, _ in x_ticks:
            px = self._data_to_px(xt, self.y_min, rect).x()
            lines.append(
                f'  <line x1="{px:.2f}" y1="{rect.top():.2f}" x2="{px:.2f}" y2="{rect.bottom():.2f}" '
                f'stroke="#E4E9F2" stroke-width="1"/>'
            )

        # Clipped data group
        lines.append('  <g clip-path="url(#plot-clip)">')
        if len(t_vis) >= 2:
            # Error polygon (matches staircase exactly)
            err_pts = []
            for ti, vi in zip(t_vis, v_vis):
                p = self._data_to_px(float(ti), float(vi), rect)
                err_pts.append(f"{p.x():.2f},{p.y():.2f}")
            p_last = self._data_to_px(float(t_vis[-1]), float(q_vis[-1]), rect)
            err_pts.append(f"{p_last.x():.2f},{p_last.y():.2f}")
            for idx in range(len(t_vis) - 1, 0, -1):
                p_h = self._data_to_px(float(t_vis[idx]), float(q_vis[idx - 1]), rect)
                p_v = self._data_to_px(float(t_vis[idx - 1]), float(q_vis[idx - 1]), rect)
                err_pts.append(f"{p_h.x():.2f},{p_h.y():.2f}")
                err_pts.append(f"{p_v.x():.2f},{p_v.y():.2f}")
            lines.append(
                f'    <polygon points="{" ".join(err_pts)}" fill="#EB4141" fill-opacity="0.24" stroke="none"/>'
            )

            # Blue original signal
            blue_pts = [
                f"{p.x():.2f},{p.y():.2f}"
                for p in (self._data_to_px(float(ti), float(vi), rect) for ti, vi in zip(t_vis, v_vis))
            ]
            lines.append(
                f'    <polyline points="{" ".join(blue_pts)}" fill="none" stroke="#1D63DC" stroke-width="2"/>'
            )

            # Red quantized staircase
            red_pts = []
            p0 = self._data_to_px(float(t_vis[0]), float(q_vis[0]), rect)
            red_pts.append(f"{p0.x():.2f},{p0.y():.2f}")
            for idx in range(1, len(t_vis)):
                ph = self._data_to_px(float(t_vis[idx]), float(q_vis[idx - 1]), rect)
                pv = self._data_to_px(float(t_vis[idx]), float(q_vis[idx]), rect)
                red_pts.append(f"{ph.x():.2f},{ph.y():.2f}")
                red_pts.append(f"{pv.x():.2f},{pv.y():.2f}")
            lines.append(
                f'    <polyline points="{" ".join(red_pts)}" fill="none" stroke="#D92B2B" stroke-width="1.8"/>'
            )
        lines.append("  </g>")

        # Plot border
        lines.append(
            f'  <rect x="{rect.left():.2f}" y="{rect.top():.2f}" width="{rect.width():.2f}" '
            f'height="{rect.height():.2f}" fill="none" stroke="#334155" stroke-width="1.5"/>'
        )

        # Y-axis header & ticks
        lines.append(
            f'  <text x="{rect.left() - 10:.2f}" y="25" text-anchor="end" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="12" font-weight="bold" fill="#334155">'
            f'Voltage | Bin | Binary</text>'
        )
        for v, _, label in y_ticks:
            py = self._data_to_px(self.x_min, v, rect).y()
            lines.append(
                f'  <line x1="{rect.left() - 6:.2f}" y1="{py:.2f}" x2="{rect.left():.2f}" y2="{py:.2f}" '
                f'stroke="#334155" stroke-width="1.2"/>'
            )
            lines.append(
                f'  <text x="{rect.left() - 10:.2f}" y="{py + 4:.2f}" text-anchor="end" '
                f'font-family="Consolas, Courier New, monospace" font-size="11.5" fill="#1E293B">{label}</text>'
            )

        # X-axis ticks & label
        for xt, xlabel in x_ticks:
            px = self._data_to_px(xt, self.y_min, rect).x()
            lines.append(
                f'  <line x1="{px:.2f}" y1="{rect.bottom():.2f}" x2="{px:.2f}" y2="{rect.bottom() + 6:.2f}" '
                f'stroke="#334155" stroke-width="1.2"/>'
            )
            lines.append(
                f'  <text x="{px:.2f}" y="{rect.bottom() + 21:.2f}" text-anchor="middle" '
                f'font-family="Segoe UI, Arial, sans-serif" font-size="11.5" fill="#1E293B">{xlabel}</text>'
            )
        lines.append(
            f'  <text x="{rect.center().x():.2f}" y="{rect.bottom() + 43:.2f}" text-anchor="middle" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="13" font-weight="bold" fill="#1E293B">Time (seconds)</text>'
        )

        # Header stats
        lines.append(
            f'  <text x="{rect.left():.2f}" y="25" text-anchor="start" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="13" font-weight="bold" fill="#0F172A">'
            f'{self.bits}-Bit ADC Quantization ({n_bins:,} bins) | Seed: {self.seed} | '
            f'Avg |Err|: {stats["avg_abs"]:.5f} V ({stats["avg_abs_bins"]:.3f} bins) | '
            f'Sum |Err|: {stats["sum_abs"]:.3f} V ({stats["sum_abs_bins"]:.1f} bins)</text>'
        )

        # Legend
        leg_x = rect.right() - 215
        leg_y = rect.top() + 10
        lines.append(
            f'  <rect x="{leg_x:.2f}" y="{leg_y:.2f}" width="205" height="66" rx="6" '
            f'fill="#FFFFFF" fill-opacity="0.92" stroke="#CBD5E1" stroke-width="1"/>'
        )
        lines.append(
            f'  <line x1="{leg_x + 10:.2f}" y1="{leg_y + 15:.2f}" x2="{leg_x + 34:.2f}" y2="{leg_y + 15:.2f}" '
            f'stroke="#1D63DC" stroke-width="2.2"/>'
            f'<text x="{leg_x + 42:.2f}" y="{leg_y + 19:.2f}" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="11" fill="#1E293B">Original Signal (0–5 V)</text>'
        )
        lines.append(
            f'  <line x1="{leg_x + 10:.2f}" y1="{leg_y + 33:.2f}" x2="{leg_x + 34:.2f}" y2="{leg_y + 33:.2f}" '
            f'stroke="#D92B2B" stroke-width="2"/>'
            f'<text x="{leg_x + 42:.2f}" y="{leg_y + 37:.2f}" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="11" fill="#1E293B">Quantized ({self.bits}-bit)</text>'
        )
        lines.append(
            f'  <rect x="{leg_x + 10:.2f}" y="{leg_y + 45:.2f}" width="24" height="11" '
            f'fill="#EB4141" fill-opacity="0.25" stroke="#EB4141" stroke-opacity="0.55" stroke-width="1"/>'
            f'<text x="{leg_x + 42:.2f}" y="{leg_y + 55:.2f}" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="11" fill="#1E293B">Quantization Error</text>'
        )

        lines.append("</svg>")
        Path(path).write_text("\n".join(lines), encoding="utf-8")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ADC Quantization Visualizer (0–5 V | 1–15 Bit)")
        self.resize(1160, 720)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # --- Top Controls Bar ---
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)

        # Bits selector (1..15)
        ctrl_row.addWidget(QLabel("<b>Bits:</b>"))
        self.bits_slider = QSlider(Qt.Orientation.Horizontal)
        self.bits_slider.setRange(1, 15)
        self.bits_slider.setValue(6)
        self.bits_slider.setFixedWidth(150)
        ctrl_row.addWidget(self.bits_slider)

        self.bits_spin = QSpinBox()
        self.bits_spin.setRange(1, 15)
        self.bits_spin.setValue(6)
        ctrl_row.addWidget(self.bits_spin)

        self.bins_label = QLabel()
        ctrl_row.addWidget(self.bins_label)

        ctrl_row.addSpacing(12)

        # Seed controls
        ctrl_row.addWidget(QLabel("<b>Seed:</b>"))
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 999999)
        self.seed_spin.setValue(42)
        ctrl_row.addWidget(self.seed_spin)

        self.btn_random_seed = QPushButton("New Random Seed")
        ctrl_row.addWidget(self.btn_random_seed)

        ctrl_row.addStretch()

        # Zoom / Pan buttons (per-axis + combined)
        self.btn_x_in = QPushButton("X +")
        self.btn_x_out = QPushButton("X -")
        self.btn_y_in = QPushButton("Y +")
        self.btn_y_out = QPushButton("Y -")
        self.btn_zoom_in = QPushButton("XY +")
        self.btn_zoom_out = QPushButton("XY -")
        self.btn_reset = QPushButton("Reset")
        for b in (
            self.btn_x_in,
            self.btn_x_out,
            self.btn_y_in,
            self.btn_y_out,
            self.btn_zoom_in,
            self.btn_zoom_out,
            self.btn_reset,
        ):
            b.setMaximumWidth(54)
            ctrl_row.addWidget(b)

        # Snapshot buttons
        self.btn_save_svg = QPushButton("Save SVG")
        self.btn_save_png = QPushButton("Save PNG")
        ctrl_row.addWidget(self.btn_save_svg)
        ctrl_row.addWidget(self.btn_save_png)

        root.addLayout(ctrl_row)

        # --- Signal Range & Average Row ---
        sig_row = QHBoxLayout()
        sig_row.setSpacing(10)

        sig_row.addWidget(QLabel("<b>Dynamic Range (V):</b>"))
        self.range_slider = QSlider(Qt.Orientation.Horizontal)
        self.range_slider.setRange(1, 500)  # 0.01 V .. 5.00 V
        self.range_slider.setValue(480)
        self.range_slider.setFixedWidth(140)
        sig_row.addWidget(self.range_slider)

        self.range_spin = QDoubleSpinBox()
        self.range_spin.setRange(0.01, 5.00)
        self.range_spin.setSingleStep(0.10)
        self.range_spin.setDecimals(2)
        self.range_spin.setValue(4.80)
        sig_row.addWidget(self.range_spin)

        sig_row.addSpacing(12)

        sig_row.addWidget(QLabel("<b>Average (V):</b>"))
        self.mean_slider = QSlider(Qt.Orientation.Horizontal)
        self.mean_slider.setRange(0, 500)  # 0.00 V .. 5.00 V
        self.mean_slider.setValue(250)
        self.mean_slider.setFixedWidth(140)
        sig_row.addWidget(self.mean_slider)

        self.mean_spin = QDoubleSpinBox()
        self.mean_spin.setRange(0.00, 5.00)
        self.mean_spin.setSingleStep(0.10)
        self.mean_spin.setDecimals(2)
        self.mean_spin.setValue(2.50)
        sig_row.addWidget(self.mean_spin)

        sig_row.addSpacing(12)
        self.range_info_label = QLabel()
        self.range_info_label.setStyleSheet("color: #334155;")
        sig_row.addWidget(self.range_info_label)
        sig_row.addStretch()

        root.addLayout(sig_row)

        # --- Vector Canvas ---
        self.canvas = QuantizationCanvas()
        root.addWidget(self.canvas, stretch=1)

        # --- Bottom Error Metrics & Help Bar ---
        stats_row = QHBoxLayout()
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("font-size: 13px; padding: 4px;")
        stats_row.addWidget(self.stats_label)
        stats_row.addStretch()
        help_lbl = QLabel("Wheel: Zoom (over axis or Shift=X, Ctrl=Y)  |  Left-Drag: Pan")
        help_lbl.setStyleSheet("color: #475569; font-size: 12px;")
        stats_row.addWidget(help_lbl)
        root.addLayout(stats_row)

        # Wire signals
        self.canvas.on_stats_changed = self._update_stats_label
        self.bits_slider.valueChanged.connect(self._on_bits_changed)
        self.bits_spin.valueChanged.connect(self._on_bits_changed)
        self.seed_spin.valueChanged.connect(self.canvas.set_seed)
        self.btn_random_seed.clicked.connect(self._randomize_seed)
        self.range_slider.valueChanged.connect(lambda v: self._on_range_changed(v / 100.0))
        self.range_spin.valueChanged.connect(self._on_range_changed)
        self.mean_slider.valueChanged.connect(lambda v: self._on_mean_changed(v / 100.0))
        self.mean_spin.valueChanged.connect(self._on_mean_changed)
        self.btn_x_in.clicked.connect(lambda: self.canvas.zoom_by(0.75, zoom_x=True, zoom_y=False))
        self.btn_x_out.clicked.connect(lambda: self.canvas.zoom_by(1.33, zoom_x=True, zoom_y=False))
        self.btn_y_in.clicked.connect(lambda: self.canvas.zoom_by(0.75, zoom_x=False, zoom_y=True))
        self.btn_y_out.clicked.connect(lambda: self.canvas.zoom_by(1.33, zoom_x=False, zoom_y=True))
        self.btn_zoom_in.clicked.connect(lambda: self.canvas.zoom_by(0.75))
        self.btn_zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1.33))
        self.btn_reset.clicked.connect(self.canvas.reset_view)
        self.btn_save_svg.clicked.connect(self._save_svg)
        self.btn_save_png.clicked.connect(self._save_png)

        self._update_bins_label(self.canvas.bits)
        self._update_range_info()
        self._update_stats_label(self.canvas.compute_visible_stats())

    def _on_bits_changed(self, value: int) -> None:
        if self.bits_slider.value() != value:
            self.bits_slider.setValue(value)
        if self.bits_spin.value() != value:
            self.bits_spin.setValue(value)
        self._update_bins_label(value)
        self.canvas.set_bits(value)
        self._update_range_info()

    def _on_range_changed(self, value: float) -> None:
        iv = int(round(value * 100))
        if self.range_slider.value() != iv:
            self.range_slider.blockSignals(True)
            self.range_slider.setValue(iv)
            self.range_slider.blockSignals(False)
        if abs(self.range_spin.value() - value) > 1e-4:
            self.range_spin.blockSignals(True)
            self.range_spin.setValue(value)
            self.range_spin.blockSignals(False)
        self.canvas.set_signal_params(value, self.mean_spin.value())
        self._update_range_info()

    def _on_mean_changed(self, value: float) -> None:
        iv = int(round(value * 100))
        if self.mean_slider.value() != iv:
            self.mean_slider.blockSignals(True)
            self.mean_slider.setValue(iv)
            self.mean_slider.blockSignals(False)
        if abs(self.mean_spin.value() - value) > 1e-4:
            self.mean_spin.blockSignals(True)
            self.mean_spin.setValue(value)
            self.mean_spin.blockSignals(False)
        self.canvas.set_signal_params(self.range_spin.value(), value)
        self._update_range_info()

    def _update_range_info(self) -> None:
        v_lo = float(np.min(self.canvas.v_orig))
        v_hi = float(np.max(self.canvas.v_orig))
        used_span = v_hi - v_lo
        pct = (used_span / 5.0) * 100.0
        used_bins = int(np.max(self.canvas.bins) - np.min(self.canvas.bins)) + 1
        total_bins = 1 << self.canvas.bits
        self.range_info_label.setText(
            f"Actual: {v_lo:.2f}–{v_hi:.2f} V ({pct:.1f}% of 5 V range | {used_bins}/{total_bins:,} bins used)"
        )

    def _update_bins_label(self, bits: int) -> None:
        n_bins = 1 << bits
        step_mv = (5.0 / (n_bins - 1)) * 1000.0
        self.bins_label.setText(f"({n_bins:,} bins | ΔV = {step_mv:.3f} mV)")

    def _randomize_seed(self) -> None:
        new_seed = int(np.random.default_rng().integers(0, 100000))
        self.seed_spin.setValue(new_seed)

    def _update_stats_label(self, stats: dict) -> None:
        self.stats_label.setText(
            f"<b>Visible Window ({stats['count']} samples):</b>  "
            f"Average |Error| = <b>{stats['avg_abs']:.6f} V</b> ({stats['avg_abs']*1000:.3f} mV | <b>{stats['avg_abs_bins']:.3f} bins</b>)  |  "
            f"Sum |Error| = <b>{stats['sum_abs']:.4f} V</b> (<b>{stats['sum_abs_bins']:.2f} bins</b>)  |  "
            f"Signed Mean = {stats['avg_signed']:+.6f} V ({stats['avg_signed_bins']:+.3f} bins)"
        )

    def _save_svg(self) -> None:
        default_name = f"quantization_{self.canvas.bits}bit_seed{self.canvas.seed}.svg"
        path, _ = QFileDialog.getSaveFileName(self, "Save Vector SVG Snapshot", default_name, "SVG Files (*.svg)")
        if path:
            self.canvas.export_svg(path)

    def _save_png(self) -> None:
        default_name = f"quantization_{self.canvas.bits}bit_seed{self.canvas.seed}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Save PNG Snapshot", default_name, "PNG Files (*.png)")
        if path:
            self.canvas.export_png(path, scale=2.0)


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
