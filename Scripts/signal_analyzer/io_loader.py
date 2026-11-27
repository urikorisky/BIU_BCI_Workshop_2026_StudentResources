"""CSV/XDF import, filtered CSV export, and analysis JSON persistence."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from .models import AnalysisState, ChannelState, Recording

_TIME_COL_ALIASES = {"time_s", "time", "t", "seconds", "time_sec", "sec"}
_LSL_COL_ALIASES = {"lsl_timestamp", "lsl_time", "timestamp", "timestamps"}


def _estimate_sample_rate(time_s: np.ndarray) -> float:
    if len(time_s) < 2:
        return 1.0
    diffs = np.diff(time_s)
    pos = diffs[np.isfinite(diffs) & (diffs > 0)]
    if len(pos) == 0:
        return 1.0
    med = float(np.median(pos))
    return (1.0 / med) if med > 0 else 1.0


def _try_float(val: str) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return float("nan")


def load_csv(path: Path) -> Recording:
    """Load a numeric multi-channel CSV file into a Recording."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        rows = [row for row in reader if row and any(cell.strip() for cell in row)]

    if not rows:
        raise ValueError(f"CSV file is empty: {path}")

    # Detect whether the first row is a header (contains any non-numeric cell)
    first_row = [c.strip() for c in rows[0]]
    has_header = False
    for cell in first_row:
        if cell == "":
            continue
        try:
            float(cell)
        except ValueError:
            has_header = True
            break

    if has_header:
        headers = [c if c else f"Col{i + 1}" for i, c in enumerate(first_row)]
        data_rows = rows[1:]
    else:
        n_cols = len(first_row)
        headers = [f"Ch{i + 1}" for i in range(n_cols)]
        data_rows = rows

    if not data_rows:
        raise ValueError(f"CSV file contains no data rows: {path}")

    n_cols = len(headers)
    parsed = np.empty((len(data_rows), n_cols), dtype=float)
    for r_idx, row in enumerate(data_rows):
        for c_idx in range(n_cols):
            val_str = row[c_idx].strip() if c_idx < len(row) else ""
            parsed[r_idx, c_idx] = _try_float(val_str)

    lower_headers = [h.strip().lower() for h in headers]
    time_col: int | None = None
    lsl_col: int | None = None
    for i, lh in enumerate(lower_headers):
        if time_col is None and lh in _TIME_COL_ALIASES:
            time_col = i
        elif lsl_col is None and lh in _LSL_COL_ALIASES:
            lsl_col = i

    excluded = {c for c in (time_col, lsl_col) if c is not None}
    signal_cols = [i for i in range(n_cols) if i not in excluded]
    if not signal_cols:
        raise ValueError("CSV file contains only time columns and no signal channels.")

    lsl_timestamps: np.ndarray | None = None
    if lsl_col is not None:
        lsl_timestamps = parsed[:, lsl_col].copy()

    if time_col is not None:
        time_s = parsed[:, time_col].copy()
    elif lsl_timestamps is not None:
        valid = lsl_timestamps[np.isfinite(lsl_timestamps)]
        t0 = float(valid[0]) if len(valid) > 0 else 0.0
        time_s = lsl_timestamps - t0
    else:
        time_s = np.arange(parsed.shape[0], dtype=float)

    raw_data = parsed[:, signal_cols]
    channel_names = [headers[i] for i in signal_cols]
    sample_rate = _estimate_sample_rate(time_s)

    return Recording(
        source_path=path,
        source_format="csv",
        stream_name=path.stem,
        stream_index=None,
        time_s=time_s,
        lsl_timestamps=lsl_timestamps,
        sample_rate=sample_rate,
        raw_data=raw_data,
        channel_names=channel_names,
        markers=[],
    )


def _stream_meta(stream: dict[str, Any], idx: int) -> tuple[str, str, float, int]:
    info = stream["info"]
    name = info.get("name", [f"Stream_{idx}"])[0]
    stype = info.get("type", ["Unknown"])[0]
    srate = float(info.get("nominal_srate", ["0"])[0] or 0.0)
    n_ch = int(info.get("channel_count", ["0"])[0] or 0)
    return str(name), str(stype), srate, n_ch


def _channel_labels(stream: dict[str, Any], n_ch: int) -> list[str]:
    try:
        ch_list = stream["info"]["desc"][0]["channels"][0]["channel"]
        labels = [str(ch.get("label", [f"Ch{i + 1}"])[0]) for i, ch in enumerate(ch_list)]
        if len(labels) == n_ch:
            return labels
    except (KeyError, IndexError, TypeError):
        pass
    return [f"Ch{i + 1}" for i in range(n_ch)]


def _is_marker_stream(stream: dict[str, Any], stype: str) -> bool:
    ts = stream.get("time_series")
    if stype.lower() in {"markers", "marker", "events", "event"}:
        return True
    if isinstance(ts, list):
        return True
    if isinstance(ts, np.ndarray) and ts.dtype.kind in {"U", "S", "O"}:
        return True
    return False


def inspect_xdf_streams(path: Path) -> list[dict[str, Any]]:
    """Load an XDF file and summarize all non-empty numeric streams."""
    import pyxdf

    path = Path(path)
    streams, _ = pyxdf.load_xdf(str(path))
    numeric_meta: list[dict[str, Any]] = []
    for idx, s in enumerate(streams):
        ts = s.get("time_stamps", [])
        if len(ts) == 0:
            continue
        name, stype, nominal_sr, n_ch = _stream_meta(s, idx)
        if _is_marker_stream(s, stype):
            continue
        t = np.asarray(ts, dtype=float)
        dur = float(t[-1] - t[0]) if len(t) >= 2 else 0.0
        eff_sr = _estimate_sample_rate(t)
        y = np.asarray(s["time_series"], dtype=float)
        actual_ch = 1 if y.ndim == 1 else int(y.shape[1])
        numeric_meta.append(
            {
                "index": idx,
                "name": name,
                "type": stype,
                "n_channels": actual_ch or n_ch,
                "n_samples": len(t),
                "duration_s": dur,
                "nominal_srate": nominal_sr,
                "effective_srate": eff_sr,
            }
        )
    return numeric_meta


def load_xdf(path: Path, stream_index: int | None = None) -> Recording:
    """Load a numeric stream (and any marker streams) from an XDF file."""
    import pyxdf

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"XDF file not found: {path}")

    streams, _ = pyxdf.load_xdf(str(path))
    if not streams:
        raise ValueError(f"No streams found in {path}")

    t_starts = [
        float(s["time_stamps"][0])
        for s in streams
        if len(s.get("time_stamps", [])) > 0
    ]
    if not t_starts:
        raise ValueError("All streams in this XDF file are empty.")
    t0 = min(t_starts)

    numeric_indices: list[int] = []
    markers: list[tuple[float, str]] = []
    for idx, s in enumerate(streams):
        if len(s.get("time_stamps", [])) == 0:
            continue
        _, stype, _, _ = _stream_meta(s, idx)
        if _is_marker_stream(s, stype):
            t_rel = np.asarray(s["time_stamps"], dtype=float) - t0
            for ti, val in zip(t_rel, s.get("time_series", [])):
                lbl = (
                    str(val[0])
                    if isinstance(val, (list, tuple, np.ndarray)) and len(val) > 0
                    else str(val)
                )
                markers.append((float(ti), lbl))
        else:
            numeric_indices.append(idx)

    if not numeric_indices:
        raise ValueError("No numeric signal streams found in this XDF file.")

    chosen_idx = numeric_indices[0] if stream_index is None else stream_index
    if chosen_idx not in numeric_indices:
        raise ValueError(f"Numeric stream index {chosen_idx} not found in {path}")

    stream = streams[chosen_idx]
    name, _, nominal_sr, _ = _stream_meta(stream, chosen_idx)
    lsl_timestamps = np.asarray(stream["time_stamps"], dtype=float)
    time_s = lsl_timestamps - t0
    raw_data = np.asarray(stream["time_series"], dtype=float)
    if raw_data.ndim == 1:
        raw_data = raw_data[:, np.newaxis]

    channel_names = _channel_labels(stream, raw_data.shape[1])
    sample_rate = _estimate_sample_rate(time_s)
    if sample_rate <= 0 and nominal_sr > 0:
        sample_rate = nominal_sr

    return Recording(
        source_path=path,
        source_format="xdf",
        stream_name=name,
        stream_index=chosen_idx,
        time_s=time_s,
        lsl_timestamps=lsl_timestamps,
        sample_rate=sample_rate,
        raw_data=raw_data,
        channel_names=channel_names,
        markers=markers,
    )


def export_filtered_csv(
    recording: Recording,
    channel_states: list[ChannelState],
    filtered_signals: list[np.ndarray],
    output_path: Path,
) -> Path:
    """Export the filtered signals of all channels to a CSV file."""
    output_path = Path(output_path)
    if output_path.suffix.lower() != ".csv":
        output_path = output_path.with_suffix(".csv")

    has_lsl = recording.lsl_timestamps is not None and len(recording.lsl_timestamps) == recording.n_samples
    header = ["time_s"]
    if has_lsl:
        header.append("lsl_timestamp")
    for ch in channel_states:
        u = ch.unit.strip()
        if u and f"({u})" not in ch.display_name:
            header.append(f"{ch.display_name} ({u})")
        else:
            header.append(ch.display_name)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for i in range(recording.n_samples):
            row: list[Any] = [f"{recording.time_s[i]:.6f}"]
            if has_lsl and recording.lsl_timestamps is not None:
                row.append(f"{recording.lsl_timestamps[i]:.6f}")
            row.extend(float(sig[i]) for sig in filtered_signals)
            writer.writerow(row)

    return output_path


def save_analysis(
    path: Path,
    recording: Recording,
    channel_states: list[ChannelState],
    ui_mode: str,
    view_state: dict[str, Any] | None = None,
) -> Path:
    """Save current analysis configuration (filters, names, scales, detectors) as JSON."""
    path = Path(path)
    if not path.name.endswith(".json"):
        path = path.with_suffix(".bci.json")

    state = AnalysisState(
        version=1,
        source_path=str(recording.source_path) if recording.source_path else None,
        source_format=recording.source_format,
        stream_index=recording.stream_index,
        stream_name=recording.stream_name,
        ui_mode=ui_mode,
        view_state=view_state or {},
        channels=channel_states,
    )
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    return path


def load_analysis(path: Path) -> AnalysisState:
    """Load an AnalysisState JSON file."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return AnalysisState.from_dict(data)
