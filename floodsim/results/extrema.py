"""Top native locations at retained output times, with bounded source reads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
from pyproj import CRS, Transformer

from floodsim.domain.geometry import AnalysisArea
from floodsim.providers.common import local_crs
from floodsim.results.regular_netcdf_source import (
    RegularNetcdfSource,
    validate_source_identity,
)
from floodsim.results.regular_queries import summary_directory
from floodsim.results.view import AdaptiveNormalizedArrays, ResultArrays
from floodsim.storage.run_store import atomic_write_json

TOP_COUNT = 10
Progress = Callable[[int, int], None]


def _peaks(
    depths: Any, velocities: Any, active: np.ndarray, times: list[int], threshold: float,
    frame_done: Callable[[], None] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Keep only per-location peaks; ties retain the earliest output index."""
    shape = active.shape
    depth_peak = np.full(shape, -np.inf)
    speed_peak = np.full(shape, -np.inf)
    depth_time = np.zeros(shape, dtype=np.int32)
    speed_time = np.zeros(shape, dtype=np.int32)
    speed_at_depth = np.full(shape, np.nan)
    depth_at_speed = np.zeros(shape)
    for time in times:
        depth = np.maximum(np.asarray(depths(time)), 0)
        speed = velocities(time)
        if speed is None:
            speed = np.full(shape, np.nan)
        valid = active & np.isfinite(depth)
        newer = valid & (depth > depth_peak)
        depth_peak[newer] = depth[newer]
        depth_time[newer] = time
        speed_at_depth[newer] = speed[newer]
        faster = valid & (depth > threshold) & np.isfinite(speed) & (speed > speed_peak)
        speed_peak[faster] = speed[faster]
        speed_time[faster] = time
        depth_at_speed[faster] = depth[faster]
        if frame_done is not None:
            frame_done()

    result: dict[str, list[dict[str, Any]]] = {}
    for metric, peak, indices in (
        ("depth", depth_peak, depth_time),
        ("speed", speed_peak, speed_time),
    ):
        flat = peak.ravel()
        valid_indices = np.flatnonzero(np.isfinite(flat) & (flat > 0))
        # Partition before sorting, preserving deterministic cell-order ties.
        if valid_indices.size > TOP_COUNT:
            cutoff = np.partition(flat[valid_indices], -TOP_COUNT)[-TOP_COUNT]
            valid_indices = valid_indices[flat[valid_indices] >= cutoff]
        order = np.lexsort((valid_indices, -flat[valid_indices]))[:TOP_COUNT]
        entries = []
        for index in valid_indices[order]:
            depth = (
                depth_peak.ravel()[index]
                if metric == "depth"
                else depth_at_speed.ravel()[index]
            )
            speed = (
                speed_at_depth.ravel()[index]
                if metric == "depth"
                else speed_peak.ravel()[index]
            )
            entries.append(
                {
                    "cell_index": int(index),
                    "time_index": int(indices.ravel()[index]),
                    "depth_m": float(depth),
                    "speed_mps": float(speed) if np.isfinite(speed) else None,
                }
            )
        result[metric] = entries
    return result


def _rank(entries: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    key = "depth_m" if metric == "depth" else "speed_mps"
    ordered = sorted(entries, key=lambda entry: (-entry[key], entry["cell_index"]))[
        :TOP_COUNT
    ]
    return [{**entry, "rank": rank} for rank, entry in enumerate(ordered, 1)]


def _locate(
    entry: dict[str, Any],
    *,
    row: float,
    col: float,
    shape: tuple[int, int],
    area: AnalysisArea,
    transformer: Transformer,
    cell_area: float,
    times: tuple[str, ...],
) -> dict[str, Any]:
    x = -area.width_m / 2 + col * area.width_m / shape[1]
    y = -area.height_m / 2 + row * area.height_m / shape[0]
    lon, lat = transformer.transform(x, y)
    return {
        **entry,
        "lon_deg": float(lon),
        "lat_deg": float(lat),
        "cell_area_m2": float(cell_area),
        "time_value": times[entry["time_index"]],
    }


def regular_extrema(
    source: RegularNetcdfSource,
    *,
    model_dir: Path,
    area: AnalysisArea,
    times: list[int],
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Read h/u/v by spatial chunk and single frame, then persist only top ten."""
    path = validate_source_identity(source, model_dir=model_dir)
    identity = json.dumps({"times": times, "area": area.model_dump()}, sort_keys=True)
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    cache = summary_directory(source, model_dir) / f"extrema-v2-{digest}.json"
    row_chunk, col_chunk = min(source.chunk_shape[1], 1024), min(source.chunk_shape[2], 1024)
    total = ((source.height + row_chunk - 1) // row_chunk
             * ((source.width + col_chunk - 1) // col_chunk) * len(times))
    done = 0

    def frame_done() -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(done, total)

    if cache.is_file():
        result = json.loads(cache.read_text(encoding="utf-8"))
        if progress is not None:
            progress(total, total)
        return result
    if progress is not None:
        progress(0, total)
    transformer = Transformer.from_crs(
        local_crs(area), CRS.from_epsg(4326), always_xy=True
    )
    collected: dict[str, list[dict[str, Any]]] = {"depth": [], "speed": []}
    # Preserve the storage chunks: splitting a compressed 400x400 frame into
    # 128x128 windows repeatedly decompresses the same bytes. Keep one frame
    # at a time, with a one-million-cell ceiling for legacy oversized chunks.
    with xr.open_dataset(path) as dataset:
        for row in range(0, source.height, row_chunk):
            for col in range(0, source.width, col_chunk):
                window = {
                    "n": slice(row, row + row_chunk),
                    "m": slice(col, col + col_chunk),
                }
                active = np.asarray(dataset["msk"].isel(window).values) > 0

                def depths(
                    time: int, cell_window: dict[str, slice] = window
                ) -> np.ndarray:
                    return np.asarray(
                        dataset["h"].isel({"time": time, **cell_window}).values
                    )

                def speeds(
                    time: int, cell_window: dict[str, slice] = window
                ) -> np.ndarray | None:
                    if not source.flow_vectors_available:
                        return None
                    return np.hypot(
                        dataset["u"].isel({"time": time, **cell_window}).values,
                        dataset["v"].isel({"time": time, **cell_window}).values,
                    )

                peaks = _peaks(
                    depths, speeds, active, times, source.display_dry_threshold_m, frame_done
                )
                for metric, entries in peaks.items():
                    for entry in entries:
                        r, c = np.unravel_index(entry["cell_index"], active.shape)
                        entry["cell_index"] = int((row + r) * source.width + col + c)
                        collected[metric].append(
                            _locate(
                                entry,
                                row=row + r + 0.5,
                                col=col + c + 0.5,
                                shape=(source.height, source.width),
                                area=area,
                                transformer=transformer,
                                cell_area=source.block_size_m**2,
                                times=source.time_values,
                            )
                        )
                    collected[metric] = _rank(collected[metric], metric)
    result = {metric: _rank(entries, metric) for metric, entries in collected.items()}
    atomic_write_json(cache, result)
    return result


def array_extrema(
    arrays: ResultArrays, *, area: AnalysisArea, times: list[int], progress: Progress | None = None,
) -> dict[str, Any]:
    """Compatibility for archived regular arrays and native adaptive faces."""
    done = 0

    def frame_done() -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(done, len(times))

    if progress is not None:
        progress(0, len(times))

    def speeds(time: int) -> np.ndarray | None:
        if arrays.velocity_u_mps is None or arrays.velocity_v_mps is None:
            return None
        speed = np.hypot(arrays.velocity_u_mps[time], arrays.velocity_v_mps[time])
        if (
            isinstance(arrays, AdaptiveNormalizedArrays)
            or arrays.velocity_grid_stride == 1
        ):
            return speed
        native = np.full(arrays.shape, np.nan)
        native[:: arrays.velocity_grid_stride, :: arrays.velocity_grid_stride] = speed
        return native

    peaks = _peaks(
        lambda time: arrays.depth_time_m[time],
        speeds,
        arrays.active_mask,
        times,
        getattr(arrays, "display_dry_threshold_m", 0.01),
        frame_done,
    )
    transformer = Transformer.from_crs(
        local_crs(area), CRS.from_epsg(4326), always_xy=True
    )
    for metric, entries in peaks.items():
        located = []
        for entry in entries:
            index = entry["cell_index"]
            if isinstance(arrays, AdaptiveNormalizedArrays):
                size = float(arrays.face_resolution_m[index])
                row0, col0 = (
                    arrays.face_row_index[index] * size,
                    arrays.face_col_index[index] * size,
                )
                row = (row0 + min(row0 + size, arrays.shape[0])) / 2
                col = (col0 + min(col0 + size, arrays.shape[1])) / 2
                cell_area = arrays.face_source_overlap_area_m2[index]
            else:
                r, c = np.unravel_index(index, arrays.shape)
                row, col = r + 0.5, c + 0.5
                cell_area = (
                    np.broadcast_to(arrays.grid_resolution_m, arrays.shape)[r, c] ** 2
                )
            located.append(
                _locate(
                    entry,
                    row=row,
                    col=col,
                    shape=arrays.shape,
                    area=area,
                    transformer=transformer,
                    cell_area=cell_area,
                    times=arrays.time_values,
                )
            )
        peaks[metric] = _rank(located, metric)
    return peaks
