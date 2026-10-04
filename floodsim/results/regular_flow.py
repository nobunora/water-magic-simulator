"""Read only visible depth and velocity; sample in the original grid frame."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from floodsim.domain.geometry import AnalysisArea
from floodsim.results.flow_field import native_flow_field
from floodsim.results.regular_netcdf_source import (
    RegularNetcdfSource,
    validate_source_identity,
)
from floodsim.results.vector_viewport import (
    _viewport_cell_bounds,
    flow_vectors_viewport_geojson,
)
from floodsim.results.view import (
    NormalizedArrays,
    ResultArtifactMissing,
    ResultTimeIndexInvalid,
)


def regular_flow_viewport(
    source: RegularNetcdfSource,
    *,
    model_dir: str | Path,
    area: AnalysisArea,
    time_index: int,
    west: float,
    south: float,
    east: float,
    north: float,
    stride: int,
    speed_scale: dict[str, Any],
    include_field: bool = False,
) -> dict[str, Any]:
    if not 0 <= time_index < len(source.time_values):
        raise ResultTimeIndexInvalid("result time index is outside available output")
    if not source.flow_vectors_available:
        raise ResultArtifactMissing("flow-vector output is not available for this run")
    empty = np.zeros((1, 1), dtype=np.float32)
    stub = NormalizedArrays(
        empty[None], empty, empty, empty.astype(bool), ("",), source.block_size_m
    )
    shape = (source.height, source.width)
    r0, r1, c0, c1 = _viewport_cell_bounds(
        stub,
        area=area,
        west=west,
        south=south,
        east=east,
        north=north,
        source_shape=shape,
    )
    if r0 == r1 or c0 == c1:
        return {
            "type": "FeatureCollection",
            "features": [],
            "metadata": {
                "arrow_count": 0,
                "speed_unit": "m/s",
                "speed_scale": speed_scale,
                "display_min_speed_mps": 0.001,
                "display_max_speed_mps": speed_scale["maximum"],
                "sampling_method": "nearest-wet-cell-to-spacing-bin-center",
                "sample_stride_cells": stride,
                "target_spacing_m": float(stride),
                "arrow_length_m": 0.8 * stride,
                "canonical_grid_spacing_m": source.block_size_m,
                "stride_anchor_row": 0,
                "stride_anchor_column": 0,
                "viewport": {
                    "west": west,
                    "south": south,
                    "east": east,
                    "north": north,
                },
            },
        }
    with xr.open_dataset(
        validate_source_identity(source, model_dir=model_dir)
    ) as dataset:
        window = {"n": slice(r0, r1), "m": slice(c0, c1)}
        depth = np.maximum(
            np.asarray(
                dataset["h"].isel({"time": time_index, **window}).values, dtype=np.float32
            ),
            0,
        )
        active = np.asarray(dataset["msk"].isel(window).values) > 0
        u = np.asarray(
            dataset["u"].isel({"time": time_index, **window}).values, dtype=np.float32
        )
        v = np.asarray(
            dataset["v"].isel({"time": time_index, **window}).values, dtype=np.float32
        )
    arrays = NormalizedArrays(
        depth[None],
        depth,
        np.zeros_like(depth),
        active,
        (source.time_values[time_index],),
        source.block_size_m,
        u[None],
        v[None],
        display_dry_threshold_m=source.display_dry_threshold_m,
    )
    payload = flow_vectors_viewport_geojson(
        arrays,
        area=area,
        time_index=0,
        west=west,
        south=south,
        east=east,
        north=north,
        stride=stride,
        source_shape=shape,
        window_origin=(r0, c0),
        speed_scale=speed_scale,
    )
    for feature in payload["features"]:
        feature["properties"]["time_index"] = time_index
    payload["metadata"]["read_window_cells"] = (r1 - r0) * (c1 - c0)
    payload["metadata"]["display_max_speed_mps"] = speed_scale["maximum"]
    if include_field and source.block_size_m == 0.5:
        payload["flow_field"] = native_flow_field(
            u, v, active & (depth >= source.display_dry_threshold_m), area=area,
            cell_size_m=source.block_size_m, window_origin=(r0, c0),
        )
    return payload
