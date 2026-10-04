"""Native-cell queries and reusable, source-identified result summaries."""

from __future__ import annotations

import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
from pyproj import CRS, Transformer

from floodsim.domain.geometry import AnalysisArea
from floodsim.providers.common import local_crs
from floodsim.results.adaptive_scale import generate_adaptive_breaks
from floodsim.results.regular_netcdf_source import (
    RegularNetcdfSource,
    validate_source_identity,
)
from floodsim.results.view import (
    NormalizedArrays,
    PointOutsideResult,
    ResultTimeIndexInvalid,
    render_depth_values_png,
    render_elevation_values_png,
    render_regular_grid_resolution_png,
)
from floodsim.storage.run_store import atomic_write_json


def summary_directory(source: RegularNetcdfSource, model_dir: str | Path) -> Path:
    path = validate_source_identity(source, model_dir=model_dir)
    return (
        Path(model_dir).parent
        / "results"
        / "query_cache"
        / (f"v1-{path.name}-{source.source_size_bytes}-{source.source_mtime_ns}")
    )


def prepare_regular_queries(
    source: RegularNetcdfSource, *, model_dir: str | Path
) -> Path:
    """Precompute native summaries once, using one spatial chunk at a time.

    The completion marker is written last; interrupted builds are never read.
    Original NetCDF outputs remain untouched.
    """
    root = summary_directory(source, model_dir)
    if (root / "complete.json").is_file():
        return root
    root.mkdir(parents=True, exist_ok=True)
    shape = (source.height, source.width)
    maps = {
        name: np.lib.format.open_memmap(
            root / f"{name}.npy", mode="w+", dtype=dtype, shape=shape
        )
        for name, dtype in (
            ("maximum", "float32"),
            ("max_time", "int32"),
            ("terrain", "float32"),
            ("active", "bool"),
            ("peak_speed", "float32"),
        )
    }
    _, row_chunk, col_chunk = source.chunk_shape
    with xr.open_dataset(
        validate_source_identity(source, model_dir=model_dir)
    ) as dataset:
        for row in range(0, source.height, row_chunk):
            for col in range(0, source.width, col_chunk):
                rows, cols = slice(row, row + row_chunk), slice(col, col + col_chunk)
                window = {"n": rows, "m": cols}
                active = np.asarray(dataset["msk"].isel(window).values) > 0
                maximum = np.full(active.shape, -np.inf, dtype=np.float32)
                max_time = np.zeros(active.shape, dtype=np.int32)
                peak = np.full(active.shape, np.nan, dtype=np.float32)
                for time in range(len(source.time_values)):
                    depth = np.maximum(
                        np.asarray(dataset["h"].isel({"time": time, **window}).values),
                        0,
                    )
                    newer = depth > maximum
                    max_time[newer] = time
                    maximum = np.fmax(maximum, depth)
                    if source.flow_vectors_available:
                        u = dataset["u"].isel({"time": time, **window}).values
                        v = dataset["v"].isel({"time": time, **window}).values
                        speed = np.hypot(u, v)
                        peak = np.fmax(
                            peak, np.where(active & (depth > source.display_dry_threshold_m), speed, np.nan)
                        )
                stored_max = (
                    dataset["hmax"].isel(window).max(dim="timemax", skipna=True).values
                )
                maps["maximum"][rows, cols] = np.where(
                    np.isfinite(stored_max), np.maximum(stored_max, 0), maximum
                )
                maps["max_time"][rows, cols] = max_time
                maps["terrain"][rows, cols] = dataset["zb"].isel(window).values
                maps["active"][rows, cols] = active
                maps["peak_speed"][rows, cols] = peak
    for array in maps.values():
        array.flush()
    speed_scale = generate_adaptive_breaks(
        maps["peak_speed"],
        np.full(shape, source.block_size_m**2, dtype=np.float32),
        zero_epsilon=0.001,
    )
    atomic_write_json(
        root / "complete.json", {"speed_scale": speed_scale.to_metadata()}
    )
    return root


@lru_cache(maxsize=2048)
def _point_cached(
    path_text: str, size: int, mtime: int, row: int, col: int
) -> dict[str, Any]:
    del size, mtime
    with xr.open_dataset(path_text) as dataset:
        cell = {"n": row, "m": col}
        if not bool(dataset["msk"].isel(cell).values > 0):
            return {"has_data": False}
        series = np.maximum(
            np.asarray(dataset["h"].isel(cell).values, dtype=np.float32), 0
        )
        maxima = np.asarray(dataset["hmax"].isel(cell).values)
        finite = maxima[np.isfinite(maxima)]
        return {
            "has_data": True,
            "series": series,
            "max_depth_m": max(0.0, float(np.max(finite)))
            if finite.size
            else float(np.nanmax(series)),
            "max_time_index": int(np.nanargmax(series)),
            "terrain_elevation_m": float(dataset["zb"].isel(cell).values),
        }


@lru_cache(maxsize=2048)
def _point_speed_cached(path_text: str, size: int, mtime: int, row: int, col: int, time: int) -> float | None:
    del size, mtime
    with xr.open_dataset(path_text) as dataset:
        cell = {"n": row, "m": col, "time": time}
        speed = float(np.hypot(dataset["u"].isel(cell).values, dataset["v"].isel(cell).values))
        return speed if np.isfinite(speed) else None


def inspect_regular_point(
    source: RegularNetcdfSource,
    *,
    model_dir: str | Path,
    area: AnalysisArea,
    lon: float,
    lat: float,
    time_index: int | None,
) -> dict[str, Any]:
    if time_index is not None and not 0 <= time_index < len(source.time_values):
        raise ResultTimeIndexInvalid("result time index is outside available output")
    transformer = Transformer.from_crs(
        CRS.from_epsg(4326), local_crs(area), always_xy=True
    )
    x, y = transformer.transform(lon, lat)
    if not (
        -area.width_m / 2 <= x < area.width_m / 2
        and -area.height_m / 2 <= y < area.height_m / 2
    ):
        raise PointOutsideResult("point is outside result bounds")
    row = int(np.floor((y + area.height_m / 2) * source.height / area.height_m))
    col = int(np.floor((x + area.width_m / 2) * source.width / area.width_m))
    root = summary_directory(source, model_dir)
    payload: dict[str, Any] = {
        "lon_deg": lon,
        "lat_deg": lat,
        "row": row,
        "column": col,
        "time_index": time_index,
        "time_value": source.time_values[time_index]
        if time_index is not None
        else None,
        **dict.fromkeys(
            (
                "depth_m",
                "max_depth_m",
                "max_time_index",
                "max_time_value",
                "terrain_elevation_m",
                "grid_resolution_m",
            )
        ),
    }
    if (root / "complete.json").is_file():
        payload["has_data"] = bool(
            np.load(root / "active.npy", mmap_mode="r")[row, col]
        )
        if payload["has_data"]:
            payload["max_depth_m"] = float(
                np.load(root / "maximum.npy", mmap_mode="r")[row, col]
            )
            payload["max_time_index"] = int(
                np.load(root / "max_time.npy", mmap_mode="r")[row, col]
            )
            payload["terrain_elevation_m"] = float(
                np.load(root / "terrain.npy", mmap_mode="r")[row, col]
            )
            if time_index is not None:
                payload["depth_m"] = _depth_cached(
                    str(validate_source_identity(source, model_dir=model_dir)),
                    source.source_size_bytes,
                    source.source_mtime_ns,
                    row,
                    col,
                    time_index,
                )
    else:
        point = _point_cached(
            str(validate_source_identity(source, model_dir=model_dir)),
            source.source_size_bytes,
            source.source_mtime_ns,
            row,
            col,
        )
        payload.update({key: value for key, value in point.items() if key != "series"})
        if point["has_data"] and time_index is not None:
            payload["depth_m"] = float(point["series"][time_index])
    if payload["has_data"]:
        if time_index is not None and source.flow_vectors_available:
            payload["speed_mps"] = 0.0 if payload["depth_m"] <= source.display_dry_threshold_m else _point_speed_cached(
                str(validate_source_identity(source, model_dir=model_dir)), source.source_size_bytes,
                source.source_mtime_ns, row, col, time_index,
            )
        payload["max_time_value"] = source.time_values[payload["max_time_index"]]
        payload["grid_resolution_m"] = source.block_size_m
    return payload


@lru_cache(maxsize=4096)
def _depth_cached(
    path: str, size: int, mtime: int, row: int, col: int, time: int
) -> float:
    del size, mtime
    with xr.open_dataset(path) as dataset:
        return max(0.0, float(dataset["h"].isel(time=time, n=row, m=col).values))


def regular_elevation_png(source: RegularNetcdfSource, model_dir: str | Path, max_px: int) -> bytes:
    path = validate_source_identity(source, model_dir=model_dir)
    root = summary_directory(source, model_dir)
    return _elevation_cached(str(path), source.source_size_bytes, source.source_mtime_ns, str(root), max_px)


@lru_cache(maxsize=8)
def _elevation_cached(path: str, size: int, mtime: int, root_text: str, max_px: int) -> bytes:
    del size, mtime
    root = Path(root_text)
    cached_png = root / f"elevation-{max_px}.png"
    if cached_png.is_file():
        return cached_png.read_bytes()
    if (root / "complete.json").is_file():
        terrain = np.load(root / "terrain.npy", mmap_mode="r")
        active = np.load(root / "active.npy", mmap_mode="r")
    else:
        with xr.open_dataset(path) as dataset:
            terrain = np.asarray(dataset["zb"].values, dtype=np.float32)
            active = np.asarray(dataset["msk"].values) > 0
    content = render_elevation_values_png(terrain, active, max_px=max_px)
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".elevation-", dir=root)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(temporary, cached_png)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return content


def regular_grid_png(source: RegularNetcdfSource, model_dir: str | Path, max_px: int) -> bytes:
    path = validate_source_identity(source, model_dir=model_dir)
    root = summary_directory(source, model_dir)
    return _grid_cached(str(path), source.source_size_bytes, source.source_mtime_ns,
                        str(root), source.block_size_m, max_px)


@lru_cache(maxsize=8)
def _grid_cached(path: str, size: int, mtime: int, root_text: str,
                 resolution_m: float, max_px: int) -> bytes:
    del size, mtime
    root = Path(root_text)
    cached_png = root / f"grid-v2-{resolution_m}-{max_px}.png"
    if cached_png.is_file():
        return cached_png.read_bytes()
    if (root / "complete.json").is_file():
        active = np.load(root / "active.npy", mmap_mode="r")
    else:
        with xr.open_dataset(path) as dataset:
            active = np.asarray(dataset["msk"].values) > 0
    content = render_regular_grid_resolution_png(active, resolution_m, max_px=max_px)
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".grid-", dir=root)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(temporary, cached_png)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return content


def saved_speed_scale(
    source: RegularNetcdfSource, model_dir: str | Path
) -> dict[str, Any] | None:
    marker = summary_directory(source, model_dir) / "complete.json"
    return (
        json.loads(marker.read_text(encoding="utf-8"))["speed_scale"]
        if marker.is_file()
        else None
    )


def regular_depth_png(source: RegularNetcdfSource, model_dir: str | Path,
                      time_index: int, max_px: int) -> bytes:
    """Selected h frame only; reuse static mask/maxima without hmax reconstruction."""
    if not 0 <= time_index < len(source.time_values):
        raise ResultTimeIndexInvalid("result time index is outside available output")
    path = validate_source_identity(source, model_dir=model_dir)
    root = prepare_regular_queries(source, model_dir=model_dir)
    return _depth_png_cached(str(path), source.source_size_bytes, source.source_mtime_ns,
                         str(root), source.block_size_m, source.display_dry_threshold_m,
                         time_index, max_px)


@lru_cache(maxsize=8)
def _depth_reference(root_text: str, resolution_m: float, dry_threshold_m: float) -> NormalizedArrays:
    root = Path(root_text)
    active = np.load(root / "active.npy", mmap_mode="r")
    maximum = np.load(root / "maximum.npy", mmap_mode="r")
    arrays = NormalizedArrays(np.empty((0, *active.shape), dtype=np.float32), maximum,
                              np.zeros(active.shape, dtype=np.float32), active, (), resolution_m,
                              display_dry_threshold_m=dry_threshold_m)
    # Compute the fixed full-run legend once, not for each selected frame.
    _ = arrays.depth_scale
    return arrays


@lru_cache(maxsize=32)
def _depth_png_cached(path: str, size: int, mtime: int, root_text: str, resolution_m: float,
                  dry_threshold_m: float, time_index: int, max_px: int) -> bytes:
    del size, mtime
    reference = _depth_reference(root_text, resolution_m, dry_threshold_m)
    with xr.open_dataset(path) as dataset:
        depth = np.asarray(dataset["h"].isel(time=time_index).values, dtype=np.float32)
    return render_depth_values_png(depth, reference.active_mask, reference.depth_scale,
                                   max_px=max_px, dry_threshold_m=dry_threshold_m)
