"""Event statistics table widget (Count, Interval Mean/SD, Processed & Raw Amplitude Mean ± SD)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .events import ROLLING_STAT_LABELS, compute_event_stats
from .models import EventDetectionResult

COLUMNS: tuple[str, ...] = (
    "Event Type",
    "Count",
    "Mean Time Interval (s)",
    "SD Time Interval (s)",
    "Frequency (Hz)",
    "BPM",
    "Amplitude (Mean ± SD)",
    "Raw Amplitude (Mean ± SD)",
)

STAT_COL_KEYS: dict[int, str] = {
    1: "count",
    2: "mean_interval_s",
    3: "sd_interval_s",
    4: "frequency_hz",
    5: "bpm",
    6: "mean_amp",
    7: "mean_raw_amp",
}


def _fmt_num(val: float | None, decimals: int = 3) -> str:
    return "—" if val is None else f"{val:.{decimals}f}"


def _fmt_mean_sd(mean: float | None, sd: float | None) -> str:
    if mean is None:
        return "—"
    sd_val = 0.0 if sd is None else sd
    return f"{mean:.3f} ± {sd_val:.3f}"


class RollingStatDialog(QDialog):
    """Configure rolling window width and step size before plotting a derived stat channel."""

    def __init__(
        self,
        event_label: str,
        stat_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plot Rolling Average Over Time")
        self.resize(360, 180)

        lay = QVBoxLayout(self)
        summary = QLabel(f"<b>{event_label}</b> — {stat_label}")
        lay.addWidget(summary)

        form = QFormLayout()
        self.window_spin = QDoubleSpinBox()
        self.window_spin.setRange(50.0, 60000.0)
        self.window_spin.setSingleStep(250.0)
        self.window_spin.setDecimals(0)
        self.window_spin.setSuffix(" ms")
        self.window_spin.setValue(5000.0)
        form.addRow("Rolling Window:", self.window_spin)

        self.step_spin = QDoubleSpinBox()
        self.step_spin.setRange(50.0, 10000.0)
        self.step_spin.setSingleStep(100.0)
        self.step_spin.setDecimals(0)
        self.step_spin.setSuffix(" ms")
        self.step_spin.setValue(500.0)
        form.addRow("Step / Bin Size:", self.step_spin)
        lay.addLayout(form)

        tip = QLabel(
            "Tip: For QRS (~1 beat/s), a 5000 ms window stepped every 500 ms "
            "captures ~5 beats per point for stable frequency/interval stats."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #6b7280; font-size: 11px;")
        lay.addWidget(tip)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class EventStatsTable(QWidget):
    """Bottom dock/panel displaying per-event-type summary statistics."""

    rolling_stat_requested = pyqtSignal(object, str, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows_data: list[dict[str, Any]] = []
        self._events: list[EventDetectionResult] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel("Event Statistics")
        title.setStyleSheet("font-weight: 600; color: #1f2937;")
        header.addWidget(title)

        hint = QLabel("(right-click any cell to plot rolling average)")
        hint.setStyleSheet("color: #6b7280; font-size: 11px; margin-left: 6px;")
        header.addWidget(hint)
        header.addStretch(1)

        self.export_btn = QPushButton("Export Table CSV")
        self.export_btn.clicked.connect(self._on_export_csv)
        self.export_btn.setVisible(False)
        header.addWidget(self.export_btn)
        layout.addLayout(header)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(90)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.table)

    def set_advanced_mode(self, advanced: bool) -> None:
        self.export_btn.setVisible(advanced)

    def update_events(self, events: list[EventDetectionResult]) -> None:
        self._events = list(events)
        self._rows_data = [compute_event_stats(res) for res in events]
        self.table.setRowCount(len(self._rows_data))

        for r, row in enumerate(self._rows_data):
            values = [
                row["event_label"],
                str(row["count"]),
                _fmt_num(row["mean_interval_s"]),
                _fmt_num(row["sd_interval_s"]),
                _fmt_num(row["frequency_hz"]),
                _fmt_num(row["bpm"], decimals=1),
                _fmt_mean_sd(row["mean_amp"], row["sd_amp"]),
                _fmt_mean_sd(row["mean_raw_amp"], row["sd_raw_amp"]),
            ]
            for c, txt in enumerate(values):
                item = QTableWidgetItem(txt)
                if c > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(r, c, item)

    def _on_context_menu(self, pos: Any) -> None:
        item = self.table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        col = item.column()
        if not (0 <= row < len(self._events)):
            return
        res = self._events[row]
        ev_label = self._rows_data[row]["event_label"]

        menu = QMenu(self)
        if col in STAT_COL_KEYS:
            stat_key = STAT_COL_KEYS[col]
            stat_lbl = ROLLING_STAT_LABELS[stat_key]
            act = menu.addAction(f"Plot rolling average of '{stat_lbl}' over time...")
            act.triggered.connect(
                lambda _checked=False, r=res, lbl=ev_label, sk=stat_key: self._prompt_rolling_stat(r, lbl, sk)
            )
        else:
            sub = menu.addMenu("Plot rolling average over time")
            for sk, stat_lbl in ROLLING_STAT_LABELS.items():
                act = sub.addAction(f"{stat_lbl}...")
                act.triggered.connect(
                    lambda _checked=False, r=res, lbl=ev_label, k=sk: self._prompt_rolling_stat(r, lbl, k)
                )

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _prompt_rolling_stat(
        self,
        res: EventDetectionResult,
        ev_label: str,
        stat_key: str,
    ) -> None:
        dlg = RollingStatDialog(ev_label, ROLLING_STAT_LABELS[stat_key], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.rolling_stat_requested.emit(
                res,
                stat_key,
                float(dlg.window_spin.value()),
                float(dlg.step_spin.value()),
            )

    def _on_export_csv(self) -> None:
        if not self._rows_data:
            QMessageBox.information(self, "Event Statistics", "No active event statistics to export.")
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Export Event Statistics CSV",
            "event_statistics.csv",
            "CSV files (*.csv);;All files (*.*)",
        )
        if not chosen:
            return
        out = Path(chosen)
        if out.suffix.lower() != ".csv":
            out = out.with_suffix(".csv")
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(list(COLUMNS))
            for r in range(self.table.rowCount()):
                writer.writerow(
                    [self.table.item(r, c).text() if self.table.item(r, c) else "" for c in range(len(COLUMNS))]
                )
        QMessageBox.information(self, "Export Complete", f"Saved statistics to:\n{out}")
