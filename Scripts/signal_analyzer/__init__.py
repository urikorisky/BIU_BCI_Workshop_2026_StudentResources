"""Modular offline biosignal analysis application package."""

from __future__ import annotations

from pathlib import Path

__all__ = ["run_app"]


def run_app(initial_path: Path | str | None = None) -> int:
    """Launch the Signal Analyzer GUI application."""
    from .app import run_app as _run_app

    return _run_app(initial_path=initial_path)
