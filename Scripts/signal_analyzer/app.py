"""Application bootstrap for the Signal Analyzer GUI."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from .main_window import SignalAnalyzerWindow


def run_app(initial_path: Path | str | None = None) -> int:
    """Create the QApplication (if needed), show SignalAnalyzerWindow, and run."""
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv)
        app.setStyle("Fusion")

    win = SignalAnalyzerWindow(initial_path=initial_path)
    win.show()

    if owns_app:
        return app.exec()
    return 0
