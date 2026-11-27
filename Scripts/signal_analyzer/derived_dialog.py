"""Dialog for creating and editing derived linear-combination channels."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .dsp import format_linear_combination_formula
from .models import ChannelState


class _TermRow(QWidget):
    """Single linear combination term: weight * source_channel[raw|filtered]."""

    def __init__(
        self,
        eligible_channels: list[ChannelState],
        weight: float = 1.0,
        channel_index: int = 0,
        source: str = "raw",
        on_changed: Any = None,
        on_remove: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_changed = on_changed

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)

        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(-1e9, 1e9)
        self.weight_spin.setDecimals(6)
        self.weight_spin.setSingleStep(0.1)
        self.weight_spin.setValue(float(weight))
        self.weight_spin.setFixedWidth(115)
        self.weight_spin.valueChanged.connect(lambda _v: self._notify())
        lay.addWidget(self.weight_spin)

        lay.addWidget(QLabel("×"))

        self.ch_combo = QComboBox()
        for ch in eligible_channels:
            lbl = f"{ch.display_name} ({ch.unit})" if ch.unit.strip() else ch.display_name
            self.ch_combo.addItem(lbl, ch.index)
        idx = self.ch_combo.findData(int(channel_index))
        if idx >= 0:
            self.ch_combo.setCurrentIndex(idx)
        self.ch_combo.currentIndexChanged.connect(lambda _i: self._notify())
        lay.addWidget(self.ch_combo, 1)

        self.src_combo = QComboBox()
        self.src_combo.addItem("Raw", "raw")
        self.src_combo.addItem("Filtered", "filtered")
        sidx = self.src_combo.findData(str(source).lower())
        if sidx >= 0:
            self.src_combo.setCurrentIndex(sidx)
        self.src_combo.currentIndexChanged.connect(lambda _i: self._notify())
        self.src_combo.setFixedWidth(90)
        lay.addWidget(self.src_combo)

        self.remove_btn = QPushButton("×")
        self.remove_btn.setFixedSize(26, 24)
        self.remove_btn.setToolTip("Remove term")
        self.remove_btn.setStyleSheet("color: #b91c1c; font-weight: bold;")
        if on_remove is not None:
            self.remove_btn.clicked.connect(lambda: on_remove(self))
        lay.addWidget(self.remove_btn)

    def _notify(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

    def to_term(self) -> dict[str, Any]:
        return {
            "channel_index": int(self.ch_combo.currentData()),
            "weight": float(self.weight_spin.value()),
            "source": str(self.src_combo.currentData()),
        }


class DerivedChannelDialog(QDialog):
    """Modal dialog for creating or editing a linear-combination derived channel."""

    def __init__(
        self,
        channels: list[ChannelState],
        initial_channel_idx: int = 0,
        existing: ChannelState | None = None,
        excluded_indices: set[int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._all_channels = channels
        self._excluded = set(excluded_indices or set())
        self._eligible = [ch for ch in channels if ch.index not in self._excluded]
        if not self._eligible:
            self._eligible = list(channels)

        self._rows: list[_TermRow] = []
        is_edit = existing is not None
        self.setWindowTitle("Edit Derived Channel" if is_edit else "Create Derived Channel")
        self.resize(580, 440)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Name + Units grid
        top_grid = QGridLayout()
        top_grid.setHorizontalSpacing(8)
        top_grid.setVerticalSpacing(6)

        top_grid.addWidget(QLabel("Channel Name:"), 0, 0)
        self.name_edit = QLineEdit()
        if existing is not None:
            self.name_edit.setText(existing.display_name)
        else:
            src_ch = next((c for c in self._eligible if c.index == initial_channel_idx), self._eligible[0])
            self.name_edit.setText(f"{src_ch.display_name} (Converted)")
        self.name_edit.textChanged.connect(self._update_preview)
        top_grid.addWidget(self.name_edit, 0, 1)

        top_grid.addWidget(QLabel("Units:"), 1, 0)
        self.unit_edit = QLineEdit()
        self.unit_edit.setPlaceholderText("e.g. μS, mV, V, BPM")
        if existing is not None:
            self.unit_edit.setText(existing.unit)
        self.unit_edit.textChanged.connect(self._update_preview)
        top_grid.addWidget(self.unit_edit, 1, 1)
        root.addLayout(top_grid)

        # Terms header + scrollable list
        terms_hdr = QHBoxLayout()
        terms_hdr.addWidget(QLabel("Linear Combination Terms (Σ weight × channel):"))
        terms_hdr.addStretch(1)
        self.add_term_btn = QPushButton("+ Add Term")
        self.add_term_btn.clicked.connect(lambda: self._add_term_row())
        terms_hdr.addWidget(self.add_term_btn)
        root.addLayout(terms_hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        scroll.setMinimumHeight(130)
        self._terms_container = QWidget()
        self._terms_layout = QVBoxLayout(self._terms_container)
        self._terms_layout.setContentsMargins(6, 6, 6, 6)
        self._terms_layout.setSpacing(4)
        self._terms_layout.addStretch(1)
        scroll.setWidget(self._terms_container)
        root.addWidget(scroll, 1)

        # Constant offset & optional reciprocal
        opts_grid = QGridLayout()
        opts_grid.setHorizontalSpacing(8)
        opts_grid.setVerticalSpacing(6)

        opts_grid.addWidget(QLabel("Constant Offset (+ b):"), 0, 0)
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-1e9, 1e9)
        self.offset_spin.setDecimals(6)
        self.offset_spin.setSingleStep(0.1)
        self.offset_spin.valueChanged.connect(self._update_preview)
        opts_grid.addWidget(self.offset_spin, 0, 1)

        self.invert_cb = QCheckBox("Reciprocal: numerator / (expression)")
        self.invert_cb.toggled.connect(self._on_invert_toggled)
        opts_grid.addWidget(self.invert_cb, 1, 0)

        num_row = QHBoxLayout()
        num_row.addWidget(QLabel("Numerator:"))
        self.num_spin = QDoubleSpinBox()
        self.num_spin.setRange(-1e12, 1e12)
        self.num_spin.setDecimals(4)
        self.num_spin.setValue(1.0)
        self.num_spin.setEnabled(False)
        self.num_spin.valueChanged.connect(self._update_preview)
        num_row.addWidget(self.num_spin, 1)
        opts_grid.addLayout(num_row, 1, 1)
        root.addLayout(opts_grid)

        hint = QLabel(
            "Linear calibration: m·Ch + b. For resistance Ω → μS: check Reciprocal with Numerator 1000000."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #4b5563; font-size: 11px;")
        root.addWidget(hint)

        # Live Formula Preview
        self.preview_lbl = QLabel()
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setStyleSheet(
            "background: #f3f4f6; border: 1px solid #d1d5db; border-radius: 4px; "
            "padding: 6px 8px; font-family: Consolas, monospace; font-size: 11px; color: #111827;"
        )
        root.addWidget(self.preview_lbl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        # Populate initial terms
        spec = (existing.derived_spec or {}) if existing is not None else {}
        terms = spec.get("terms") if spec.get("kind") == "linear_combination" else None
        if terms:
            for t in terms:
                self._add_term_row(
                    weight=float(t.get("weight", 1.0)),
                    channel_index=int(t.get("channel_index", initial_channel_idx)),
                    source=str(t.get("source", "raw")),
                )
            self.offset_spin.setValue(float(spec.get("offset", 0.0)))
            inv = bool(spec.get("invert", False))
            self.invert_cb.setChecked(inv)
            self.num_spin.setValue(float(spec.get("invert_numerator", 1.0)))
            self.num_spin.setEnabled(inv)
        else:
            default_idx = (
                initial_channel_idx
                if any(c.index == initial_channel_idx for c in self._eligible)
                else self._eligible[0].index
            )
            self._add_term_row(weight=1.0, channel_index=default_idx, source="raw")

        self._update_preview()

    def _add_term_row(
        self,
        weight: float = 1.0,
        channel_index: int | None = None,
        source: str = "raw",
    ) -> None:
        if channel_index is None:
            channel_index = self._eligible[0].index
        row = _TermRow(
            eligible_channels=self._eligible,
            weight=weight,
            channel_index=channel_index,
            source=source,
            on_changed=self._update_preview,
            on_remove=self._remove_term_row,
        )
        self._rows.append(row)
        self._terms_layout.insertWidget(self._terms_layout.count() - 1, row)
        self._sync_remove_buttons()
        self._update_preview()

    def _remove_term_row(self, row: _TermRow) -> None:
        if len(self._rows) <= 1:
            return
        self._rows.remove(row)
        self._terms_layout.removeWidget(row)
        row.deleteLater()
        self._sync_remove_buttons()
        self._update_preview()

    def _sync_remove_buttons(self) -> None:
        can_remove = len(self._rows) > 1
        for row in self._rows:
            row.remove_btn.setEnabled(can_remove)

    def _on_invert_toggled(self, checked: bool) -> None:
        self.num_spin.setEnabled(checked)
        self._update_preview()

    def _build_spec(self) -> dict[str, Any]:
        return {
            "kind": "linear_combination",
            "terms": [r.to_term() for r in self._rows],
            "offset": float(self.offset_spin.value()),
            "invert": bool(self.invert_cb.isChecked()),
            "invert_numerator": float(self.num_spin.value()),
        }

    def _update_preview(self) -> None:
        names = [c.display_name for c in self._all_channels]
        formula = format_linear_combination_formula(
            self._build_spec(),
            names,
            unit=self.unit_edit.text().strip(),
        )
        ch_name = self.name_edit.text().strip() or "Derived"
        self.preview_lbl.setText(f"{ch_name} = {formula}")

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Invalid Name", "Please enter a non-empty channel name.")
            return
        if not self._rows:
            QMessageBox.warning(self, "No Terms", "Please add at least one channel term.")
            return
        self.accept()

    def result_config(self) -> tuple[str, str, dict[str, Any]]:
        return (
            self.name_edit.text().strip(),
            self.unit_edit.text().strip(),
            self._build_spec(),
        )
