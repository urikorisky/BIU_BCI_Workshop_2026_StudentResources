"""Crisp PyQt6 QPainter vector icons for toolbar buttons."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF


def _make_icon(draw_fn: Callable[[QPainter, QColor], None], color: QColor | str = "#222222") -> QIcon:
    qcolor = QColor(color)
    pixmap = QPixmap(48, 48)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw_fn(painter, qcolor)
    painter.end()
    return QIcon(pixmap)


def _pen(color: QColor, width: float = 3.2) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def camera_icon(color: QColor | str = "#222222") -> QIcon:
    """Camera icon for Save Snapshot / Save as Image."""

    def _draw(painter: QPainter, c: QColor) -> None:
        painter.setPen(_pen(c, 3.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(6, 14, 36, 24), 4.0, 4.0)
        painter.drawPolyline(
            QPolygonF([QPointF(16, 14), QPointF(19, 8), QPointF(29, 8), QPointF(32, 14)])
        )
        painter.drawEllipse(QPointF(24, 26), 7.5, 7.5)
        painter.setBrush(c)
        painter.drawEllipse(QPointF(35.5, 19.5), 1.4, 1.4)

    return _make_icon(_draw, color)


def export_icon(color: QColor | str = "#222222") -> QIcon:
    """Page with folded corner and arrow coming out to the right."""

    def _draw(painter: QPainter, c: QColor) -> None:
        painter.setPen(_pen(c, 3.2))
        painter.drawPolyline(
            QPolygonF([
                QPointF(26, 20),
                QPointF(26, 14),
                QPointF(18, 6),
                QPointF(8, 6),
                QPointF(8, 42),
                QPointF(26, 42),
                QPointF(26, 32),
            ])
        )
        painter.drawPolyline(
            QPolygonF([QPointF(18, 6), QPointF(18, 14), QPointF(26, 14)])
        )
        painter.drawLine(QPointF(19, 26), QPointF(41, 26))
        painter.drawPolyline(
            QPolygonF([QPointF(34, 19), QPointF(41, 26), QPointF(34, 33)])
        )

    return _make_icon(_draw, color)


def open_icon(color: QColor | str = "#222222") -> QIcon:
    """Folder icon for Import Data."""

    def _draw(painter: QPainter, c: QColor) -> None:
        painter.setPen(_pen(c, 3.2))
        painter.drawPolyline(
            QPolygonF([
                QPointF(6, 14),
                QPointF(6, 38),
                QPointF(42, 38),
                QPointF(42, 17),
                QPointF(23, 17),
                QPointF(19, 11),
                QPointF(9, 11),
                QPointF(6, 14),
            ])
        )

    return _make_icon(_draw, color)


def save_icon(color: QColor | str = "#222222") -> QIcon:
    """Floppy-disk icon for Save Analysis."""

    def _draw(painter: QPainter, c: QColor) -> None:
        painter.setPen(_pen(c, 3.2))
        painter.drawPolyline(
            QPolygonF([
                QPointF(8, 8),
                QPointF(34, 8),
                QPointF(40, 14),
                QPointF(40, 40),
                QPointF(8, 40),
                QPointF(8, 8),
            ])
        )
        painter.drawRect(QRectF(15, 8, 14, 11))
        painter.drawRect(QRectF(14, 26, 20, 14))

    return _make_icon(_draw, color)


def load_icon(color: QColor | str = "#222222") -> QIcon:
    """Document with downward-inward arrow for Load Analysis."""

    def _draw(painter: QPainter, c: QColor) -> None:
        painter.setPen(_pen(c, 3.2))
        painter.drawPolyline(
            QPolygonF([
                QPointF(10, 8),
                QPointF(28, 8),
                QPointF(38, 18),
                QPointF(38, 40),
                QPointF(10, 40),
                QPointF(10, 8),
            ])
        )
        painter.drawLine(QPointF(24, 18), QPointF(24, 33))
        painter.drawPolyline(
            QPolygonF([QPointF(18, 27), QPointF(24, 33), QPointF(30, 27)])
        )

    return _make_icon(_draw, color)


def reset_icon(color: QColor | str = "#222222") -> QIcon:
    """Circular refresh arrow for Reset View."""

    def _draw(painter: QPainter, c: QColor) -> None:
        painter.setPen(_pen(c, 3.2))
        painter.drawArc(QRectF(9, 9, 30, 30), 35 * 16, 290 * 16)
        painter.drawPolyline(
            QPolygonF([QPointF(32, 10), QPointF(39, 13), QPointF(36, 20)])
        )

    return _make_icon(_draw, color)
