"""Viewport flow-vector sampling at a display-space target density."""

from __future__ import annotations

from typing import Any

import numpy as np
from pyproj import CRS, Transformer

from floodsim.domain.geometry import AnalysisArea
from floodsim.providers.common import local_crs
from floodsim.results.view import (
    DISPLAY_DRY_THRESHOLD_M,
    AdaptiveNormalizedArrays,
    NormalizedArrays,
    ResultArrays,
    ResultArtifactMissing,
    ResultTimeIndexInvalid,
    ResultViewError,
)

_MIN_SPEED_MPS = 0.001


def _result_speed_range(arrays: ResultArrays, time_index: int) -> tuple[float, float]:
    """Range from every stored velocity sample, not only the visible arrows."""
    assert arrays.velocity_u_mps is not None and arrays.velocity_v_mps is not None
    speed = np.hypot(arrays.velocity_u_mps[time_index], arrays.velocity_v_mps[time_index])
    valid = np.isfinite(speed) & (speed >= _MIN_SPEED_MPS)
    if not np.any(valid):
        return _MIN_SPEED_MPS, _MIN_SPEED_MPS
    return float(np.min(speed[valid])), float(np.max(speed[valid]))


def _viewport_cell_bounds(
    arrays: ResultArrays,
    *,
    area: AnalysisArea,
    west: float,
    south: float,
    east: float,
    north: float,
    source_shape: tuple[int, int] | None = None,
) -> tuple[int, int, int, int]:
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ResultViewError("viewport bounds are invalid")
    to_local = Transformer.from_crs(CRS.from_epsg(4326), local_crs(area), always_xy=True)
    corners = [
        to_local.transform(west, south),
        to_local.transform(west, north),
        to_local.transform(east, south),
        to_local.transform(east, north),
    ]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    xmin = -area.width_m / 2.0
    ymin = -area.height_m / 2.0
    height, width = source_shape or arrays.shape
    cell_width_m = area.width_m / float(width)
    cell_height_m = area.height_m / float(height)
    col0 = max(0, min(width, int(np.floor((min(xs) - xmin) / cell_width_m))))
    col1 = max(0, min(width, int(np.ceil((max(xs) - xmin) / cell_width_m))))
    row0 = max(0, min(height, int(np.floor((min(ys) - ymin) / cell_height_m))))
    row1 = max(0, min(height, int(np.ceil((max(ys) - ymin) / cell_height_m))))
    return row0, row1, col0, col1


def _face_lookup(arrays: AdaptiveNormalizedArrays) -> np.ndarray:
    lookup = np.full(arrays.shape, -1, dtype=np.int32)
    for face in range(arrays.face_resolution_m.size):
        size = int(arrays.face_resolution_m[face])
        r0 = int(arrays.face_row_index[face]) * size
        c0 = int(arrays.face_col_index[face]) * size
        r1 = min(arrays.source_height_cells, r0 + size)
        c1 = min(arrays.source_width_cells, c0 + size)
        if r1 > r0 and c1 > c0:
            lookup[r0:r1, c0:c1] = face
    return lookup


def flow_vectors_viewport_geojson(
    arrays: ResultArrays,
    *,
    area: AnalysisArea,
    time_index: int,
    west: float,
    south: float,
    east: float,
    north: float,
    stride: int,
    min_speed_mps: float = _MIN_SPEED_MPS,
    source_shape: tuple[int, int] | None = None,
    window_origin: tuple[int, int] = (0, 0),
    speed_scale: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return viewport samples at ``stride`` metres, independent of grid size."""
    if stride < 1:
        raise ResultViewError("stride must be a positive integer")
    if time_index < 0 or time_index >= arrays.depth_time_m.shape[0]:
        raise ResultTimeIndexInvalid(f"time_index {time_index} is outside available output")
    if arrays.velocity_u_mps is None or arrays.velocity_v_mps is None:
        raise ResultArtifactMissing("flow-vector output is not available for this run")
    speed_min_mps, speed_max_mps = _result_speed_range(arrays, time_index)

    row0, row1, col0, col1 = _viewport_cell_bounds(
        arrays, area=area, west=west, south=south, east=east, north=north, source_shape=source_shape
    )
    to_wgs84 = Transformer.from_crs(local_crs(area), CRS.from_epsg(4326), always_xy=True)
    xmin = -area.width_m / 2.0
    ymin = -area.height_m / 2.0
    adaptive = isinstance(arrays, AdaptiveNormalizedArrays)
    if adaptive:
        assert isinstance(arrays, AdaptiveNormalizedArrays)
        face_lookup = _face_lookup(arrays)
    else:
        face_lookup = None
    height, width = source_shape or arrays.shape
    cell_width_m = area.width_m / float(width)
    cell_height_m = area.height_m / float(height)
    cell_spacing_m = min(cell_width_m, cell_height_m)
    stride_cells = max(1, round(float(stride) / cell_spacing_m))
    arrow_length_m = 0.8 * float(stride)
    features: list[dict[str, Any]] = []

    # Align bins to the result grid so panning does not make arrows jump between
    # unrelated samples. Each bin selects the visible wet cell nearest its
    # centre instead of testing only the bin anchor. This keeps positions even
    # while ensuring that a dry anchor cannot hide flow elsewhere in the bin.
    first_bin_row = (row0 // stride_cells) * stride_cells
    first_bin_col = (col0 // stride_cells) * stride_cells
    for bin_row in range(first_bin_row, row1, stride_cells):
        for bin_col in range(first_bin_col, col1, stride_cells):
            sample_row0 = max(row0, bin_row)
            sample_row1 = min(row1, bin_row + stride_cells)
            sample_col0 = max(col0, bin_col)
            sample_col1 = min(col1, bin_col + stride_cells)
            if sample_row0 >= sample_row1 or sample_col0 >= sample_col1:
                continue

            if adaptive:
                assert isinstance(arrays, AdaptiveNormalizedArrays)
                assert face_lookup is not None
                candidate_faces = np.unique(
                    face_lookup[sample_row0:sample_row1, sample_col0:sample_col1]
                )
                candidate_faces = candidate_faces[candidate_faces >= 0]
                if candidate_faces.size == 0:
                    continue
                depths = arrays.depth_time_m[time_index, candidate_faces]
                us = arrays.velocity_u_mps[time_index, candidate_faces]
                vs = arrays.velocity_v_mps[time_index, candidate_faces]
                speeds = np.hypot(us, vs)
                valid = (
                    arrays.active_mask[candidate_faces]
                    & np.isfinite(depths)
                    & (depths >= getattr(arrays, "display_dry_threshold_m", DISPLAY_DRY_THRESHOLD_M))
                    & np.isfinite(speeds)
                    & (speeds >= min_speed_mps)
                )
                if not np.any(valid):
                    continue
                center_row = (sample_row0 + sample_row1 - 1) / 2.0
                center_col = (sample_col0 + sample_col1 - 1) / 2.0
                face_rows = (
                    arrays.face_row_index[candidate_faces]
                    * arrays.face_resolution_m[candidate_faces]
                    + arrays.face_resolution_m[candidate_faces] / 2.0
                )
                face_cols = (
                    arrays.face_col_index[candidate_faces]
                    * arrays.face_resolution_m[candidate_faces]
                    + arrays.face_resolution_m[candidate_faces] / 2.0
                )
                distances = np.square(face_rows - center_row) + np.square(
                    face_cols - center_col
                )
                best = int(np.argmin(np.where(valid, distances, np.inf)))
                face = int(candidate_faces[best])
                depth = float(depths[best])
                u = float(us[best])
                v = float(vs[best])
                grid_resolution = float(arrays.face_resolution_m[face])
                row = int(arrays.face_row_index[face]) * int(grid_resolution)
                col = int(arrays.face_col_index[face]) * int(grid_resolution)
                row += int(grid_resolution) // 2
                col += int(grid_resolution) // 2
            else:
                assert isinstance(arrays, NormalizedArrays)
                r0, r1 = sample_row0 - window_origin[0], sample_row1 - window_origin[0]
                c0, c1 = sample_col0 - window_origin[1], sample_col1 - window_origin[1]
                rows = np.arange(r0, r1)
                cols = np.arange(c0, c1)
                velocity_rows = np.minimum(
                    arrays.velocity_u_mps.shape[1] - 1,
                    rows // arrays.velocity_grid_stride,
                )
                velocity_cols = np.minimum(
                    arrays.velocity_u_mps.shape[2] - 1,
                    cols // arrays.velocity_grid_stride,
                )
                depths = arrays.depth_time_m[
                    time_index, r0:r1, c0:c1
                ]
                us = arrays.velocity_u_mps[time_index][np.ix_(velocity_rows, velocity_cols)]
                vs = arrays.velocity_v_mps[time_index][np.ix_(velocity_rows, velocity_cols)]
                speeds = np.hypot(us, vs)
                valid = (
                    arrays.active_mask[
                        r0:r1, c0:c1
                    ]
                    & np.isfinite(depths)
                    & (depths >= getattr(arrays, "display_dry_threshold_m", DISPLAY_DRY_THRESHOLD_M))
                    & np.isfinite(speeds)
                    & (speeds >= min_speed_mps)
                )
                if not np.any(valid):
                    continue
                local_rows, local_cols = np.indices(valid.shape)
                center_row = (valid.shape[0] - 1) / 2.0
                center_col = (valid.shape[1] - 1) / 2.0
                distances = np.square(local_rows - center_row) + np.square(
                    local_cols - center_col
                )
                best_row, best_col = np.unravel_index(
                    int(np.argmin(np.where(valid, distances, np.inf))), valid.shape
                )
                row = sample_row0 + int(best_row)
                col = sample_col0 + int(best_col)
                depth = float(depths[best_row, best_col])
                u = float(us[best_row, best_col])
                v = float(vs[best_row, best_col])
                grid_resolution = (
                    float(arrays.grid_resolution_m)
                    if np.ndim(arrays.grid_resolution_m) == 0
                    else float(np.asarray(arrays.grid_resolution_m)[row - window_origin[0], col - window_origin[1]])
                )

            speed = float(np.hypot(u, v))
            if (
                not np.isfinite(depth)
                or depth < getattr(arrays, "display_dry_threshold_m", DISPLAY_DRY_THRESHOLD_M)
                or not np.isfinite(speed)
                or speed < min_speed_mps
            ):
                continue

            dx = u / speed
            dy = v / speed
            cx = xmin + (col + 0.5) * cell_width_m
            cy = ymin + (row + 0.5) * cell_height_m
            tail = (cx - dx * arrow_length_m * 0.42, cy - dy * arrow_length_m * 0.42)
            tip = (cx + dx * arrow_length_m * 0.58, cy + dy * arrow_length_m * 0.58)
            head = arrow_length_m * 0.28
            angle = np.deg2rad(30.0)
            ca, sa = float(np.cos(angle)), float(np.sin(angle))
            bx, by = -dx, -dy
            left = (tip[0] + (bx * ca - by * sa) * head, tip[1] + (bx * sa + by * ca) * head)
            right = (tip[0] + (bx * ca + by * sa) * head, tip[1] + (-bx * sa + by * ca) * head)

            def lonlat(point: tuple[float, float]) -> list[float]:
                lon, lat = to_wgs84.transform(*point)
                return [float(lon), float(lat)]

            props: dict[str, Any] = {
                "speed_mps": speed,
                "u_mps": u,
                "v_mps": v,
                "time_index": time_index,
                "row": row,
                "column": col,
                "grid_resolution_m": grid_resolution,
            }
            if adaptive:
                props["face_index"] = face
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [
                        [lonlat(tail), lonlat(tip)],
                        [lonlat(tip), lonlat(left)],
                        [lonlat(tip), lonlat(right)],
                    ],
                },
                "properties": props,
            })

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "speed_unit": "m/s",
            "min_speed_mps": float(min_speed_mps),
            "display_min_speed_mps": speed_min_mps,
            "display_max_speed_mps": speed_max_mps,
            "speed_scale": speed_scale if speed_scale is not None else arrays.speed_scale.to_metadata(),
            "sample_stride_cells": stride_cells,
            "target_spacing_m": float(stride),
            "arrow_length_m": arrow_length_m,
            "arrow_count": len(features),
            "sampling_method": "nearest-wet-cell-to-spacing-bin-center",
            "canonical_grid_spacing_m": cell_spacing_m,
            "stride_anchor_row": 0,
            "stride_anchor_column": 0,
            "viewport": {"west": west, "south": south, "east": east, "north": north},
        },
    }
