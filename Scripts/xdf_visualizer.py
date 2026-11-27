"""
Quick XDF (Extensible Data Format) Visualizer using pyxdf + matplotlib.

Usage:
    python xdf_visualizer.py path/to/recording.xdf
    python xdf_visualizer.py          # opens a file-picker dialog

Requirements:
    pip install pyxdf numpy matplotlib
"""

from __future__ import annotations

import csv
import re
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
        _attach_export_toolbar(fig, path, streams, t0)
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
    _attach_export_toolbar(fig, path, streams, t0)
    plt.show()


def _safe_filename_part(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return cleaned or fallback


def export_streams_to_csv(
    streams: list[dict[str, Any]],
    t0: float,
    output_path: Path,
) -> list[tuple[Path, int]]:
    """Export all non-empty XDF streams to CSV file(s).

    If there is a single non-empty stream, writes directly to ``output_path``.
    If there are multiple streams, writes one CSV per stream using ``output_path``
    as the base name (``<stem>_<idx>_<stream_name>.csv``).
    """
    non_empty = [
        (idx, s)
        for idx, s in enumerate(streams)
        if len(s.get("time_stamps", [])) > 0
    ]
    if not non_empty:
        raise ValueError("No non-empty streams to export.")

    if output_path.suffix.lower() != ".csv":
        output_path = output_path.with_suffix(".csv")

    written: list[tuple[Path, int]] = []
    multi = len(non_empty) > 1

    for idx, s in non_empty:
        name, stype, _, _ = _stream_meta(s, idx)
        if multi:
            safe_name = _safe_filename_part(name, f"stream_{idx}")
            dest = output_path.with_name(f"{output_path.stem}_{idx}_{safe_name}.csv")
        else:
            dest = output_path

        timestamps = np.asarray(s["time_stamps"], dtype=float)
        t_rel = timestamps - t0
        is_marker = _is_marker_stream(s, stype)

        with dest.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_marker:
                raw_vals = s.get("time_series", [])
                rows_list = [
                    list(v) if isinstance(v, (list, tuple, np.ndarray)) else [v]
                    for v in raw_vals
                ]
                n_cols = max((len(r) for r in rows_list), default=1)
                marker_headers = (
                    ["marker"]
                    if n_cols == 1
                    else [f"marker_{c + 1}" for c in range(n_cols)]
                )
                writer.writerow(["time_s", "lsl_timestamp", *marker_headers])
                for i, row in enumerate(rows_list):
                    padded = [str(row[c]) if c < len(row) else "" for c in range(n_cols)]
                    writer.writerow([f"{t_rel[i]:.6f}", f"{timestamps[i]:.6f}", *padded])
            else:
                y = np.asarray(s["time_series"], dtype=float)
                if y.ndim == 1:
                    y = y[:, np.newaxis]
                labels = _channel_labels(s, y.shape[1])
                writer.writerow(["time_s", "lsl_timestamp", *labels])
                for i in range(len(timestamps)):
                    writer.writerow(
                        [f"{t_rel[i]:.6f}", f"{timestamps[i]:.6f}", *y[i].tolist()]
                    )

        written.append((dest, len(timestamps)))

    return written


def _make_qt_icon(draw_fn: Any, color: Any) -> Any:
    from matplotlib.backends.qt_compat import QtGui

    pixmap = QtGui.QPixmap(48, 48)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pixmap)
    aa = getattr(QtGui.QPainter, "RenderHint", QtGui.QPainter).Antialiasing
    painter.setRenderHint(aa, True)
    draw_fn(painter, color)
    painter.end()
    return QtGui.QIcon(pixmap)


def _qt_pen(color: Any, width: float = 3.2) -> Any:
    from matplotlib.backends.qt_compat import QtCore, QtGui

    pen = QtGui.QPen(color, width)
    cap = getattr(QtCore.Qt, "PenCapStyle", QtCore.Qt).RoundCap
    join = getattr(QtCore.Qt, "PenJoinStyle", QtCore.Qt).RoundJoin
    pen.setCapStyle(cap)
    pen.setJoinStyle(join)
    return pen


def _draw_camera_icon_qt(painter: Any, color: Any) -> None:
    from matplotlib.backends.qt_compat import QtCore, QtGui

    painter.setPen(_qt_pen(color, 3.2))
    painter.setBrush(QtGui.QBrush(QtCore.Qt.BrushStyle.NoBrush if hasattr(QtCore.Qt, "BrushStyle") else QtCore.Qt.NoBrush))
    # Camera body
    painter.drawRoundedRect(QtCore.QRectF(6, 14, 36, 24), 4.0, 4.0)
    # Top viewfinder bump
    bump = QtGui.QPolygonF([
        QtCore.QPointF(16, 14),
        QtCore.QPointF(19, 8),
        QtCore.QPointF(29, 8),
        QtCore.QPointF(32, 14),
    ])
    painter.drawPolyline(bump)
    # Lens
    painter.drawEllipse(QtCore.QPointF(24, 26), 7.5, 7.5)
    # Flash dot
    painter.setBrush(QtGui.QBrush(color))
    painter.drawEllipse(QtCore.QPointF(35.5, 19.5), 1.4, 1.4)


def _draw_export_icon_qt(painter: Any, color: Any) -> None:
    from matplotlib.backends.qt_compat import QtCore, QtGui

    painter.setPen(_qt_pen(color, 3.2))
    # Page outline with folded top-right corner and opening on right for arrow
    page_top = QtGui.QPolygonF([
        QtCore.QPointF(26, 20),
        QtCore.QPointF(26, 14),
        QtCore.QPointF(18, 6),
        QtCore.QPointF(8, 6),
        QtCore.QPointF(8, 42),
        QtCore.QPointF(26, 42),
        QtCore.QPointF(26, 32),
    ])
    painter.drawPolyline(page_top)
    fold = QtGui.QPolygonF([
        QtCore.QPointF(18, 6),
        QtCore.QPointF(18, 14),
        QtCore.QPointF(26, 14),
    ])
    painter.drawPolyline(fold)
    # Arrow coming out of the page to the right
    painter.drawLine(QtCore.QPointF(19, 26), QtCore.QPointF(41, 26))
    arrowhead = QtGui.QPolygonF([
        QtCore.QPointF(34, 19),
        QtCore.QPointF(41, 26),
        QtCore.QPointF(34, 33),
    ])
    painter.drawPolyline(arrowhead)


def _make_tk_icon(draw_fn: Any) -> Any:
    from PIL import Image, ImageDraw, ImageTk

    img = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw_fn(draw, (40, 40, 40, 255))
    img = img.resize((22, 22), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(img)


def _draw_camera_icon_pil(draw: Any, color: tuple[int, int, int, int]) -> None:
    draw.rounded_rectangle([6, 14, 42, 38], radius=4, outline=color, width=3)
    draw.line([(16, 14), (19, 8), (29, 8), (32, 14)], fill=color, width=3, joint="curve")
    draw.ellipse([16.5, 18.5, 31.5, 33.5], outline=color, width=3)
    draw.ellipse([34, 18, 37, 21], fill=color)


def _draw_export_icon_pil(draw: Any, color: tuple[int, int, int, int]) -> None:
    draw.line(
        [(26, 20), (26, 14), (18, 6), (8, 6), (8, 42), (26, 42), (26, 32)],
        fill=color,
        width=3,
        joint="curve",
    )
    draw.line([(18, 6), (18, 14), (26, 14)], fill=color, width=3, joint="curve")
    draw.line([(19, 26), (41, 26)], fill=color, width=3)
    draw.line([(34, 19), (41, 26), (34, 33)], fill=color, width=3, joint="curve")


def _attach_export_toolbar(
    fig: Any,
    path: Path,
    streams: list[dict[str, Any]],
    t0: float,
) -> None:
    """Customize the toolbar: camera icon for Save, plus an Export CSV button."""
    manager = getattr(fig.canvas, "manager", None)
    toolbar = getattr(manager, "toolbar", None)
    if toolbar is None:
        return

    # Qt backend (PyQt5 / PyQt6 / PySide2 / PySide6)
    if hasattr(toolbar, "_actions") and "save_figure" in toolbar._actions:
        from matplotlib.backends.qt_compat import QtGui, QtWidgets

        fg_color = toolbar.palette().color(toolbar.foregroundRole())
        save_action = toolbar._actions["save_figure"]
        save_action.setIcon(_make_qt_icon(_draw_camera_icon_qt, fg_color))
        save_action.setToolTip("Save as image")

        export_action = QtGui.QAction(
            _make_qt_icon(_draw_export_icon_qt, fg_color),
            "Export CSV",
            toolbar,
        )
        export_action.setToolTip("Export streams to CSV")

        def _on_export_qt() -> None:
            default_out = str(path.with_suffix(".csv"))
            chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
                toolbar,
                "Export streams to CSV",
                default_out,
                "CSV files (*.csv);;All files (*.*)",
            )
            if not chosen:
                return
            try:
                written = export_streams_to_csv(streams, t0, Path(chosen))
            except Exception as exc:
                QtWidgets.QMessageBox.critical(
                    toolbar, "CSV Export Failed", f"Could not export CSV:\n{exc}"
                )
                return
            summary = "\n".join(f"• {p.name} ({n} samples)" for p, n in written)
            QtWidgets.QMessageBox.information(
                toolbar, "CSV Export Complete", f"Exported:\n{summary}"
            )

        export_action.triggered.connect(_on_export_qt)
        toolbar.addAction(export_action)
        return

    # Tkinter backend fallback
    if hasattr(toolbar, "_buttons") and "Save" in toolbar._buttons:
        import tkinter as tk
        from tkinter import filedialog, messagebox

        cam_icon = _make_tk_icon(_draw_camera_icon_pil)
        exp_icon = _make_tk_icon(_draw_export_icon_pil)
        toolbar._custom_icons = [cam_icon, exp_icon]

        save_btn = toolbar._buttons["Save"]
        save_btn.configure(image=cam_icon)

        def _on_export_tk() -> None:
            chosen = filedialog.asksaveasfilename(
                title="Export streams to CSV",
                initialdir=str(path.parent),
                initialfile=f"{path.stem}.csv",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
            if not chosen:
                return
            try:
                written = export_streams_to_csv(streams, t0, Path(chosen))
            except Exception as exc:
                messagebox.showerror("CSV Export Failed", f"Could not export CSV:\n{exc}")
                return
            summary = "\n".join(f"• {p.name} ({n} samples)" for p, n in written)
            messagebox.showinfo("CSV Export Complete", f"Exported:\n{summary}")

        export_btn = tk.Button(
            master=toolbar,
            image=exp_icon,
            command=_on_export_tk,
            relief=tk.FLAT,
            padx=2,
            pady=2,
        )
        export_btn.pack(side=tk.LEFT)


def main() -> None:
    """Launch the full Signal Analyzer GUI (accepting an optional .xdf/.csv/.bci.json path)."""
    from signal_analyzer import run_app

    initial_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if initial_path is not None and not initial_path.is_file():
        raise SystemExit(f"File not found: {initial_path}")
    raise SystemExit(run_app(initial_path=initial_path))


if __name__ == "__main__":
    main()
