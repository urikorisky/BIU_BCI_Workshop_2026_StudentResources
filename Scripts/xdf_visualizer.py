"""
Quick XDF (Extensible Data Format) Visualizer using pyxdf + matplotlib.

Usage:
    python xdf_visualizer.py path/to/recording.xdf
    python xdf_visualizer.py          # opens a file-picker dialog

Requirements:
    pip install pyxdf numpy matplotlib
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

try:
    import pyxdf
except ImportError as exc:
    raise SystemExit(
        "Missing dependency 'pyxdf'. Install it with:\n    pip install pyxdf"
    ) from exc


def pick_xdf_file() -> Path:
    """Open a native file dialog to choose an .xdf file."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    selected = filedialog.askopenfilename(
        title="Select an XDF recording",
        filetypes=[("XDF files", "*.xdf"), ("All files", "*.*")],
    )
    root.destroy()
    if not selected:
        raise SystemExit("No XDF file selected.")
    return Path(selected)


def _stream_meta(stream: dict[str, Any], idx: int) -> tuple[str, str, float, int]:
    info = stream["info"]
    name = info.get("name", [f"Stream_{idx}"])[0]
    stype = info.get("type", ["Unknown"])[0]
    srate = float(info.get("nominal_srate", ["0"])[0] or 0.0)
    n_ch = int(info.get("channel_count", ["0"])[0] or 0)
    return name, stype, srate, n_ch


def _channel_labels(stream: dict[str, Any], n_ch: int) -> list[str]:
    try:
        ch_list = stream["info"]["desc"][0]["channels"][0]["channel"]
        labels = [ch.get("label", [f"Ch {i + 1}"])[0] for i, ch in enumerate(ch_list)]
        if len(labels) == n_ch:
            return labels
    except (KeyError, IndexError, TypeError):
        pass
    return [f"Ch {i + 1}" for i in range(n_ch)]


def _is_marker_stream(stream: dict[str, Any], stype: str) -> bool:
    ts = stream.get("time_series")
    if stype.lower() in {"markers", "marker", "events", "event"}:
        return True
    if isinstance(ts, list):
        return True
    if isinstance(ts, np.ndarray) and ts.dtype.kind in {"U", "S", "O"}:
        return True
    return False


def summarize_streams(streams: list[dict[str, Any]]) -> None:
    print(f"\nLoaded {len(streams)} stream(s):")
    for idx, stream in enumerate(streams):
        name, stype, srate, n_ch = _stream_meta(stream, idx)
        t = np.asarray(stream.get("time_stamps", []), dtype=float)
        dur = float(t[-1] - t[0]) if len(t) >= 2 else 0.0
        eff_srate = (len(t) - 1) / dur if dur > 0 else 0.0
        print(
            f"  [{idx}] {name!r} | type={stype} | ch={n_ch} | "
            f"samples={len(t)} | dur={dur:.2f}s | nominal={srate:.1f} Hz | eff={eff_srate:.1f} Hz"
        )


def plot_xdf(path: Path) -> None:
    streams, header = pyxdf.load_xdf(str(path))
    if not streams:
        raise SystemExit(f"No streams found in {path}")

    summarize_streams(streams)

    # Global t0 across all non-empty streams so everything shares a common 0.0 s origin
    t_starts = [
        float(s["time_stamps"][0])
        for s in streams
        if len(s.get("time_stamps", [])) > 0
    ]
    if not t_starts:
        raise SystemExit("All streams in this XDF file are empty.")
    t0 = min(t_starts)

    numeric_streams: list[tuple[int, dict[str, Any]]] = []
    marker_streams: list[tuple[int, dict[str, Any]]] = []
    for idx, s in enumerate(streams):
        if len(s.get("time_stamps", [])) == 0:
            continue
        _, stype, _, _ = _stream_meta(s, idx)
        if _is_marker_stream(s, stype):
            marker_streams.append((idx, s))
        else:
            numeric_streams.append((idx, s))

    if not numeric_streams:
        # Marker-only XDF fallback: plot marker timeline
        fig, ax = plt.subplots(figsize=(12, 3.5))
        for idx, s in marker_streams:
            name, _, _, _ = _stream_meta(s, idx)
            t_rel = np.asarray(s["time_stamps"], dtype=float) - t0
            labels = [str(row[0]) if isinstance(row, (list, tuple, np.ndarray)) else str(row) for row in s["time_series"]]
            ax.vlines(t_rel, 0, 1, colors="crimson", linestyles="--", alpha=0.8)
            for ti, lbl in zip(t_rel, labels):
                ax.text(ti, 1.02, lbl, rotation=45, ha="left", va="bottom", fontsize=8)
            ax.set_title(f"{path.name} — Marker Stream: {name}")
        ax.set_xlabel("Time (s)")
        ax.set_yticks([])
        plt.tight_layout()
        plt.show()
        return

    n_plots = len(numeric_streams)
    fig, axes = plt.subplots(
        n_plots, 1, sharex=True, figsize=(13, max(3.2 * n_plots, 4.5)), squeeze=False
    )

    for row_idx, (stream_idx, s) in enumerate(numeric_streams):
        ax = axes[row_idx, 0]
        name, stype, srate, n_ch = _stream_meta(s, stream_idx)
        t_rel = np.asarray(s["time_stamps"], dtype=float) - t0
        y = np.asarray(s["time_series"], dtype=float)
        if y.ndim == 1:
            y = y[:, np.newaxis]
        labels = _channel_labels(s, y.shape[1])

        if y.shape[1] == 1:
            ax.plot(t_rel, y[:, 0], linewidth=1.0, label=labels[0])
            ax.set_ylabel("Amplitude")
        else:
            # Demean and stack multi-channel signals with a clean vertical offset
            y_centered = y - np.nanmedian(y, axis=0, keepdims=True)
            scale = float(np.nanpercentile(np.abs(y_centered), 98)) or 1.0
            spacing = 2.5 * scale
            offsets = np.arange(y.shape[1])[::-1] * spacing
            for ch in range(y.shape[1]):
                ax.plot(t_rel, y_centered[:, ch] + offsets[ch], linewidth=0.9)
            ax.set_yticks(offsets)
            ax.set_yticklabels(labels)
            ax.set_ylabel("Channel (demeaned)")

        ax.set_title(f"{name} ({stype}, {y.shape[1]} ch, {srate:.0f} Hz)", loc="left", fontsize=10)
        ax.grid(True, alpha=0.25)

    # Overlay any marker streams on all numeric subplots
    marker_colors = ["crimson", "darkorange", "purple", "teal"]
    for m_i, (stream_idx, s) in enumerate(marker_streams):
        color = marker_colors[m_i % len(marker_colors)]
        t_rel = np.asarray(s["time_stamps"], dtype=float) - t0
        raw_vals = s["time_series"]
        for ti, val in zip(t_rel, raw_vals):
            lbl = str(val[0]) if isinstance(val, (list, tuple, np.ndarray)) and len(val) > 0 else str(val)
            for row_idx in range(n_plots):
                ax = axes[row_idx, 0]
                ax.axvline(ti, color=color, linestyle="--", linewidth=0.9, alpha=0.7)
                if row_idx == 0:
                    y_top = ax.get_ylim()[1]
                    ax.text(
                        ti,
                        y_top,
                        f" {lbl}",
                        color=color,
                        fontsize=8,
                        rotation=45,
                        va="bottom",
                        ha="left",
                    )

    axes[-1, 0].set_xlabel("Time since recording start (s)")
    fig.suptitle(f"XDF Recording: {path.name}", fontsize=12, fontweight="bold")
    plt.tight_layout()
    plt.show()


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else pick_xdf_file()
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")
    plot_xdf(path)


if __name__ == "__main__":
    main()
