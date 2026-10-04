"""Validated, bounded description of a regular SFINCS NetCDF result."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from floodsim.results.adaptive_scale import (
    AdaptiveScaleResult,
    generate_adaptive_breaks,
)

DESCRIPTOR_SCHEMA = "regular-netcdf-source-v1"
CACHE_SCHEMA_REVISION = 1


def regular_speed_reference(
    source: RegularNetcdfSource, *, model_dir: str | Path,
) -> AdaptiveScaleResult:
    """Area-weighted per-cell peak speed, scanning bounded spatial/time chunks."""
    path = validate_source_identity(source, model_dir=model_dir)
    histogram: dict[float, float] = {}
    _, row_chunk, col_chunk = source.chunk_shape
    with xr.open_dataset(path) as dataset:
        if source.flow_vectors_available:
            for row in range(0, source.height, row_chunk):
                for col in range(0, source.width, col_chunk):
                    window = {"n": slice(row, row + row_chunk), "m": slice(col, col + col_chunk)}
                    active = np.asarray(dataset["msk"].isel(window).values) > 0
                    peak = np.full(active.shape, np.nan)
                    for time in range(len(source.time_values)):
                        depth = np.asarray(dataset["h"].isel({"time": time, **window}).values)
                        u = np.asarray(dataset["u"].isel({"time": time, **window}).values)
                        v = np.asarray(dataset["v"].isel({"time": time, **window}).values)
                        speed = np.hypot(u, v)
                        wet = active & np.isfinite(depth) & (depth > source.display_dry_threshold_m) & np.isfinite(speed)
                        peak = np.fmax(peak, np.where(wet, speed, np.nan))
                    values, counts = np.unique(peak[np.isfinite(peak)], return_counts=True)
                    for value, count in zip(values, counts, strict=True):
                        histogram[float(value)] = histogram.get(float(value), 0.0) + float(count) * source.block_size_m ** 2
    return generate_adaptive_breaks(np.array(list(histogram)), np.array(list(histogram.values())), zero_epsilon=0.001)


class RegularNetcdfSourceError(RuntimeError):
    """A retained regular-grid source cannot satisfy the result contract."""


@dataclass(frozen=True)
class RegularNetcdfSource:
    """Portable source descriptor; it intentionally contains no grid values."""

    source_filename: str
    source_size_bytes: int
    source_mtime_ns: int
    height: int
    width: int
    block_size_m: float
    bounds: dict[str, float]
    time_values: tuple[str, ...]
    variable_names: dict[str, str]
    chunk_shape: tuple[int, int, int]
    flow_vectors_available: bool
    display_dry_threshold_m: float = 0.01

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": DESCRIPTOR_SCHEMA,
            "source_filename": self.source_filename,
            "source_size_bytes": self.source_size_bytes,
            "source_mtime_ns": self.source_mtime_ns,
            "grid": {
                "height": self.height,
                "width": self.width,
                "block_size_m": self.block_size_m,
                "bounds": self.bounds,
            },
            "time_values": list(self.time_values),
            "variable_names": self.variable_names,
            "chunk_shape": list(self.chunk_shape),
            "flow_vectors_available": self.flow_vectors_available,
            "display_dry_threshold_m": self.display_dry_threshold_m,
            "cache_schema_revision": CACHE_SCHEMA_REVISION,
        }


def inspect_regular_netcdf_source(
    source_path: str | Path,
    *,
    model_dir: str | Path,
    bounds: dict[str, float],
    block_size_m: float,
    display_dry_threshold_m: float = 0.01,
) -> RegularNetcdfSource:
    """Inspect dimensions and encoding without materialising a result grid."""
    path = Path(source_path).resolve()
    root = Path(model_dir).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RegularNetcdfSourceError("NetCDF source must be below the model directory") from exc
    if not path.is_file() or block_size_m <= 0:
        raise RegularNetcdfSourceError("NetCDF source or grid block size is invalid")

    try:
        with xr.open_dataset(path) as dataset:
            _require_dims(dataset, "h", ("time", "n", "m"))
            _require_dims(dataset, "hmax", ("timemax", "n", "m"))
            _require_dims(dataset, "zb", ("n", "m"))
            _require_dims(dataset, "msk", ("n", "m"))
            has_u = "u" in dataset.data_vars
            has_v = "v" in dataset.data_vars
            if has_u != has_v:
                raise RegularNetcdfSourceError("SFINCS velocity output must contain both u and v")
            if has_u:
                _require_dims(dataset, "u", ("time", "n", "m"))
                _require_dims(dataset, "v", ("time", "n", "m"))
            chunk_shape = dataset["h"].encoding.get("chunksizes") or dataset["h"].shape
            if len(chunk_shape) != 3 or any(int(value) <= 0 for value in chunk_shape):
                raise RegularNetcdfSourceError("SFINCS depth chunk shape is invalid")
            height, width = int(dataset.sizes["n"]), int(dataset.sizes["m"])
            if height <= 0 or width <= 0 or not int(dataset.sizes["time"]):
                raise RegularNetcdfSourceError("SFINCS result dimensions are invalid")
            times = tuple(str(value) for value in dataset["time"].values)
    except RegularNetcdfSourceError:
        raise
    except (OSError, ValueError, KeyError) as exc:
        raise RegularNetcdfSourceError("SFINCS NetCDF result is unreadable") from exc

    stat = path.stat()
    return RegularNetcdfSource(
        source_filename=relative.as_posix(),
        source_size_bytes=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
        height=height,
        width=width,
        block_size_m=float(block_size_m),
        bounds={key: float(value) for key, value in bounds.items()},
        time_values=times,
        variable_names={
            "h": "h", "hmax": "hmax", "zb": "zb", "msk": "msk",
            **({"u": "u", "v": "v"} if has_u else {}),
        },
        chunk_shape=(int(chunk_shape[0]), int(chunk_shape[1]), int(chunk_shape[2])),
        flow_vectors_available=has_u,
        display_dry_threshold_m=display_dry_threshold_m,
    )


def validate_source_identity(descriptor: RegularNetcdfSource, *, model_dir: str | Path) -> Path:
    """Resolve a descriptor only within its model directory and verify identity."""
    root = Path(model_dir).resolve()
    candidate = (root / descriptor.source_filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RegularNetcdfSourceError("NetCDF descriptor path escapes the model directory") from exc
    try:
        stat = candidate.stat()
    except OSError as exc:
        raise RegularNetcdfSourceError("NetCDF source is missing") from exc
    if stat.st_size != descriptor.source_size_bytes or stat.st_mtime_ns != descriptor.source_mtime_ns:
        raise RegularNetcdfSourceError("NetCDF source identity does not match its descriptor")
    return candidate


def load_regular_netcdf_descriptor(path: str | Path) -> RegularNetcdfSource:
    """Load a persisted descriptor without accepting unknown schema or paths."""
    import json

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != DESCRIPTOR_SCHEMA:
            raise RegularNetcdfSourceError("NetCDF descriptor schema is unsupported")
        grid = payload["grid"]
        names = payload["variable_names"]
        descriptor = RegularNetcdfSource(
            source_filename=str(payload["source_filename"]),
            source_size_bytes=int(payload["source_size_bytes"]),
            source_mtime_ns=int(payload["source_mtime_ns"]),
            height=int(grid["height"]), width=int(grid["width"]),
            block_size_m=float(grid["block_size_m"]),
            bounds={key: float(value) for key, value in grid["bounds"].items()},
            time_values=tuple(str(value) for value in payload["time_values"]),
            variable_names={str(key): str(value) for key, value in names.items()},
            chunk_shape=(
                int(payload["chunk_shape"][0]),
                int(payload["chunk_shape"][1]),
                int(payload["chunk_shape"][2]),
            ),
            flow_vectors_available=bool(payload["flow_vectors_available"]),
            display_dry_threshold_m=float(payload.get("display_dry_threshold_m", 0.01)),
        )
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise RegularNetcdfSourceError("NetCDF descriptor is invalid") from exc
    if (
        not np.isfinite(descriptor.display_dry_threshold_m)
        or descriptor.display_dry_threshold_m <= 0
        or len(descriptor.chunk_shape) != 3
        or any(value <= 0 for value in descriptor.chunk_shape)
        or descriptor.source_filename.startswith("/")
        or ".." in Path(descriptor.source_filename).parts
    ):
        raise RegularNetcdfSourceError("NetCDF descriptor has invalid dimensions or path")
    return descriptor


def read_regular_window(
    descriptor: RegularNetcdfSource,
    *,
    model_dir: str | Path,
    time_index: int,
    row_slice: slice,
    col_slice: slice,
) -> dict[str, np.ndarray]:
    """Read only a selected time and spatial window; never decode full h."""
    if time_index < 0 or time_index >= len(descriptor.time_values):
        raise RegularNetcdfSourceError("result time index is outside available output")
    path = validate_source_identity(descriptor, model_dir=model_dir)
    rows = _bounded_slice(row_slice, descriptor.height)
    cols = _bounded_slice(col_slice, descriptor.width)
    try:
        with xr.open_dataset(path) as dataset:
            h = np.asarray(dataset["h"].isel(time=time_index, n=rows, m=cols).values, dtype=np.float32)
            msk = np.asarray(dataset["msk"].isel(n=rows, m=cols).values) > 0
            zb = np.asarray(dataset["zb"].isel(n=rows, m=cols).values, dtype=np.float32)
            hmax = np.asarray(dataset["hmax"].isel(n=rows, m=cols).max(dim="timemax", skipna=True).values, dtype=np.float32)
            missing = msk & ~np.isfinite(hmax)
            if np.any(missing):
                reconstructed = np.full(hmax.shape, np.nan, dtype=np.float32)
                for frame in range(len(descriptor.time_values)):
                    sample = np.asarray(dataset["h"].isel(time=frame, n=rows, m=cols).values, dtype=np.float32)
                    reconstructed = np.fmax(reconstructed, sample)
                hmax[missing] = reconstructed[missing]
            result = {"depth": h, "active_mask": msk, "terrain": zb, "max_depth": hmax}
            if descriptor.flow_vectors_available:
                result["u"] = np.asarray(dataset["u"].isel(time=time_index, n=rows, m=cols).values, dtype=np.float32)
                result["v"] = np.asarray(dataset["v"].isel(time=time_index, n=rows, m=cols).values, dtype=np.float32)
            return result
    except (OSError, ValueError, KeyError) as exc:
        raise RegularNetcdfSourceError("NetCDF viewport read failed") from exc


def read_regular_point_depth_series(
    descriptor: RegularNetcdfSource,
    *,
    model_dir: str | Path,
    row: int,
    column: int,
) -> np.ndarray:
    """Read every retained depth at one native cell without decoding the grid."""
    if row < 0 or row >= descriptor.height or column < 0 or column >= descriptor.width:
        raise RegularNetcdfSourceError("result point is outside available output")
    path = validate_source_identity(descriptor, model_dir=model_dir)
    try:
        with xr.open_dataset(path) as dataset:
            return np.asarray(
                dataset["h"].isel(n=row, m=column).values,
                dtype=np.float32,
            )
    except (OSError, ValueError, KeyError) as exc:
        raise RegularNetcdfSourceError("NetCDF point-series read failed") from exc


def regular_window_arrays(
    descriptor: RegularNetcdfSource,
    *,
    model_dir: str | Path,
    time_index: int,
) -> Any:
    """Adapt one regular source frame to the legacy renderer without a 3-D read."""
    from floodsim.results.view import NormalizedArrays

    values = read_regular_window(
        descriptor,
        model_dir=model_dir,
        time_index=time_index,
        row_slice=slice(0, descriptor.height),
        col_slice=slice(0, descriptor.width),
    )
    depth = values["depth"]
    active = values["active_mask"]
    velocity_u = values.get("u")
    velocity_v = values.get("v")
    depth = np.where(active, np.maximum(depth, 0.0), np.nan).astype(np.float32, copy=False)
    maximum = np.where(active, np.maximum(values["max_depth"], 0.0), np.nan).astype(np.float32, copy=False)
    return NormalizedArrays(
        depth_time_m=depth[None, :, :],
        max_depth_m=maximum,
        terrain_elevation_m=values["terrain"],
        active_mask=active,
        time_values=(descriptor.time_values[time_index],),
        grid_resolution_m=descriptor.block_size_m,
        velocity_u_mps=(velocity_u[None, :, :] if velocity_u is not None else None),
        velocity_v_mps=(velocity_v[None, :, :] if velocity_v is not None else None),
        display_dry_threshold_m=descriptor.display_dry_threshold_m,
    )


def _bounded_slice(value: slice, limit: int) -> slice:
    start = 0 if value.start is None else max(0, int(value.start))
    stop = limit if value.stop is None else min(limit, int(value.stop))
    if start >= stop:
        raise RegularNetcdfSourceError("NetCDF viewport does not intersect the result")
    return slice(start, stop)


def scan_regular_diagnostics(source: RegularNetcdfSource, *, model_dir: str | Path) -> dict[str, Any]:
    """Scan terrain, mask and maximum depth one source chunk at a time."""
    path = validate_source_identity(source, model_dir=model_dir)
    time_chunk, row_chunk, col_chunk = source.chunk_shape
    active_cells = 0
    finite_hmax_cells = 0
    reconstructed_cells = 0
    global_max = 0.0
    minimum_visible_depth = np.inf
    terrain_min = np.inf
    terrain_max = -np.inf
    try:
        with xr.open_dataset(path) as dataset:
            for row_start in range(0, source.height, row_chunk):
                rows = slice(row_start, min(source.height, row_start + row_chunk))
                for col_start in range(0, source.width, col_chunk):
                    cols = slice(col_start, min(source.width, col_start + col_chunk))
                    active = np.asarray(dataset["msk"].isel(n=rows, m=cols).values) > 0
                    terrain = np.asarray(dataset["zb"].isel(n=rows, m=cols).values)
                    if np.any(~np.isfinite(terrain[active])):
                        raise RegularNetcdfSourceError("active SFINCS terrain cells contain non-finite values")
                    if np.any(active):
                        terrain_min = min(terrain_min, float(np.min(terrain[active])))
                        terrain_max = max(terrain_max, float(np.max(terrain[active])))
                    active_cells += int(np.count_nonzero(active))
                    maximum = np.full(active.shape, np.nan, dtype=np.float32)
                    for time_start in range(0, int(dataset.sizes["timemax"]), time_chunk):
                        values = np.asarray(dataset["hmax"].isel(timemax=slice(time_start, time_start + time_chunk), n=rows, m=cols).values)
                        finite = np.isfinite(values)
                        if np.any(np.isinf(values[:, active])):
                            raise RegularNetcdfSourceError("active SFINCS maximum depth contains infinite values")
                        if np.any(values[:, active][finite[:, active]] < 0.0):
                            raise RegularNetcdfSourceError("active SFINCS maximum depth contains negative finite values")
                        if values.size:
                            maximum = np.fmax(maximum, np.where(finite, values, np.nan).max(axis=0))
                    finite_hmax = np.isfinite(maximum) & active
                    finite_hmax_cells += int(np.count_nonzero(finite_hmax))
                    missing = active & ~finite_hmax
                    if np.any(missing):
                        reconstructed_cells += int(np.count_nonzero(missing))
                        for time_start in range(0, int(dataset.sizes["time"]), time_chunk):
                            depth = np.asarray(dataset["h"].isel(time=slice(time_start, time_start + time_chunk), n=rows, m=cols).values)
                            if np.any(~np.isfinite(depth[:, active])):
                                raise RegularNetcdfSourceError("active SFINCS depth cells contain non-finite values")
                            maximum[missing] = np.maximum(maximum[missing], np.max(depth[:, missing], axis=0))
                    if np.any(active):
                        global_max = max(global_max, float(np.nanmax(np.where(active, maximum, np.nan))))
                    visible = maximum[active & np.isfinite(maximum) & (maximum >= source.display_dry_threshold_m)]
                    if visible.size:
                        minimum_visible_depth = min(minimum_visible_depth, float(np.min(visible)))
    except RegularNetcdfSourceError:
        raise
    except (OSError, ValueError, KeyError) as exc:
        raise RegularNetcdfSourceError("SFINCS NetCDF diagnostics could not be read") from exc
    return {
        "active_cells": active_cells,
        "finite_hmax_cells": finite_hmax_cells,
        "hmax_reconstructed_cells": reconstructed_cells,
        "global_max_depth_m": global_max,
        "min_visible_depth_m": source.display_dry_threshold_m if not np.isfinite(minimum_visible_depth) else minimum_visible_depth,
        "terrain_min_elevation_m": terrain_min,
        "terrain_max_elevation_m": terrain_max,
    }


def _require_dims(dataset: xr.Dataset, name: str, expected: tuple[str, ...]) -> None:
    if name not in dataset.data_vars or tuple(dataset[name].dims) != expected:
        raise RegularNetcdfSourceError(f"SFINCS result variable {name} has incompatible dimensions")
