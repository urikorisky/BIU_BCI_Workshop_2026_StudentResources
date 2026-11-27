"""
Offline Biosignal Analyzer (CSV & XDF) using PyQt6 + PyQtGraph.

Usage:
    python signal_analyzer.py
    python signal_analyzer.py path/to/recording.csv
    python signal_analyzer.py path/to/recording.xdf
    python signal_analyzer.py path/to/saved.bci.json

Requirements:
    pip install PyQt6 pyqtgraph numpy scipy pyxdf
"""

from __future__ import annotations

import sys
from pathlib import Path

from signal_analyzer import run_app


def main() -> None:
    initial_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if initial_path is not None and not initial_path.is_file():
        raise SystemExit(f"File not found: {initial_path}")
    raise SystemExit(run_app(initial_path=initial_path))


if __name__ == "__main__":
    main()
