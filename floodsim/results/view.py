"""Deterministic visualization and native inspection for normalized results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cached_property
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image
from pyproj import CRS, Transformer

from floodsim.domain.geometry import AnalysisArea
from floodsim.providers.common import local_crs
from floodsim.results.adaptive_scale import (
    AdaptiveScaleResult,
    color_indices,
    generate_adaptive_breaks,
    scale_legend,
)

DISPLAY_DRY_THRESHOLD_M = 0.01
MIN_RENDER_PX = 256
MAX_RENDER_PX = 4096


class ResultViewError(RuntimeError):
    code = "RESULT_VIEW_FAILED"
    retryable = False


class ResultArtifactMissing(ResultViewError):
    code = "RESULT_ARTIFACT_MISSING"


class ResultTimeIndexInvalid(ResultViewError):
    code = "RESULT_TIME_INDEX_INVALID"


class PointOutsideResult(ResultViewError):
    code = "POINT_OUTSIDE_RESULT"


@dataclass(frozen=True)
class DepthBand:
    label: str
    minimum_m: float
    maximum_m: float | None
    rgba: tuple[int, int, int, int]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "min_m": self.minimum_m,
            "max_m": self.maximum_m,
            "color": "#{:02X}{:02X}{:02X}".format(*self.rgba[:3]),
        }


# Fixed thresholds keep shallow flooding legible even when a small area is deep.
# Colors are centralized so server-rendered PNG and metadata stay identical.
DEPTH_BANDS: tuple[DepthBand, ...] = (
    DepthBand("0.00–0.05 m", 0.00, 0.05, (198, 232, 255, 210)),
    DepthBand("0.05–0.10 m", 0.05, 0.10, (91, 177, 255, 215)),
    DepthBand("0.10–0.20 m", 0.10, 0.20, (64, 110, 222, 220)),
    DepthBand("0.20–0.40 m", 0.20, 0.40, (126, 82, 196, 225)),
    DepthBand("0.40–0.80 m", 0.40, 0.80, (196, 65, 139, 230)),
    DepthBand("0.80–1.60 m", 0.80, 1.60, (109, 27, 74, 235)),
    DepthBand("1.60 m以上", 1.60, None, (73, 18, 52, 240)),
)

GRID_RESOLUTION_COLORS: dict[float, tuple[int, int, int, int]] = {
    0.5: (76, 120, 168, 210),
    1: (38, 70, 83, 210),
    2: (42, 111, 151, 210),
    4: (61, 145, 128, 210),
    8: (122, 168, 116, 210),
    16: (186, 188, 125, 210),
    32: (208, 200, 173, 210),
}

ELEVATION_COLORS: tuple[tuple[int, int, int, int], ...] = (
    (49, 54, 149, 220),
    (69, 117, 180, 220),
    (0, 183, 212, 220),
    (26, 152, 80, 220),
    (253, 231, 37, 220),
    (253, 174, 97, 220),
    (244, 109, 67, 220),
    (165, 0, 38, 220),
)


@dataclass(frozen=True)
class NormalizedArrays:
    depth_time_m: np.ndarray
    max_depth_m: np.ndarray
    terrain_elevation_m: np.ndarray
    active_mask: np.ndarray
    time_values: tuple[str, ...]
    grid_resolution_m: np.ndarray | float
    velocity_u_mps: np.ndarray | None = None
    velocity_v_mps: np.ndarray | None = None
    velocity_grid_stride: int = 1
    display_dry_threshold_m: float = DISPLAY_DRY_THRESHOLD_M

    @cached_property
    def depth_scale(self) -> AdaptiveScaleResult:
        return result_scale(self, self.max_depth_m, zero_epsilon=self.display_dry_threshold_m)

    @cached_property
    def elevation_scale(self) -> AdaptiveScaleResult:
        return result_scale(self, self.terrain_elevation_m, anchor_zero=False)

    @cached_property
    def speed_scale(self) -> AdaptiveScaleResult:
        return result_speed_scale(self)

    @property
    def shape(self) -> tuple[int, int]:
        return self.max_depth_m.shape



@dataclass(frozen=True)
class AdaptiveNormalizedArrays:
    """Native quadtree-face result plus mapping to the source 1 m analysis grid."""

    depth_time_m: np.ndarray
    max_depth_m: np.ndarray
    terrain_elevation_m: np.ndarray
    active_mask: np.ndarray
    time_values: tuple[str, ...]
    face_resolution_m: np.ndarray
    face_row_index: np.ndarray
    face_col_index: np.ndarray
    face_source_overlap_area_m2: np.ndarray
    source_height_cells: int
    source_width_cells: int
    velocity_u_mps: np.ndarray | None = None
    velocity_v_mps: np.ndarray | None = None

    @cached_property
    def depth_scale(self) -> AdaptiveScaleResult:
        return result_scale(self, self.max_depth_m, zero_epsilon=DISPLAY_DRY_THRESHOLD_M)

    @cached_property
    def elevation_scale(self) -> AdaptiveScaleResult:
        return result_scale(self, self.terrain_elevation_m, anchor_zero=False)

    @cached_property
    def speed_scale(self) -> AdaptiveScaleResult:
        return result_speed_scale(self)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.source_height_cells, self.source_width_cells)


ResultArrays = NormalizedArrays | AdaptiveNormalizedArrays


def maximum_depth_location(arrays: ResultArrays, *, area: AnalysisArea) -> dict[str, float]:
    """Locate the first deepest finite active native cell, never a display pixel."""
    valid = arrays.active_mask & np.isfinite(arrays.max_depth_m)
    if not np.any(valid):
        return {}
    index = int(np.argmax(np.where(valid, arrays.max_depth_m, -np.inf)))
    if isinstance(arrays, AdaptiveNormalizedArrays):
        row0, row1, col0, col1 = _adaptive_face_bounds(arrays, index)
        row, col = (row0 + row1) / 2, (col0 + col1) / 2
    else:
        r, c = np.unravel_index(index, arrays.shape)
        row, col = float(r) + 0.5, float(c) + 0.5
    x = -area.width_m / 2 + col * area.width_m / arrays.shape[1]
    y = -area.height_m / 2 + row * area.height_m / arrays.shape[0]
    transformer = Transformer.from_crs(local_crs(area), CRS.from_epsg(4326), always_xy=True)
    lon, lat = transformer.transform(x, y)
    return {"global_max_lon_deg": float(lon), "global_max_lat_deg": float(lat)}


def result_scale(arrays: ResultArrays, values: np.ndarray, **options: Any) -> AdaptiveScaleResult:
    areas = arrays.face_source_overlap_area_m2 if isinstance(arrays, AdaptiveNormalizedArrays) else np.broadcast_to(arrays.grid_resolution_m, arrays.shape) ** 2
    return generate_adaptive_breaks(np.where(arrays.active_mask, values, np.nan), areas, **options)


def result_speed_scale(arrays: ResultArrays) -> AdaptiveScaleResult:
    if arrays.velocity_u_mps is None or arrays.velocity_v_mps is None:
        return generate_adaptive_breaks(np.array([]))
    peak = np.full(arrays.velocity_u_mps.shape[1:], np.nan)
    for index in range(arrays.velocity_u_mps.shape[0]):
        speed = np.hypot(arrays.velocity_u_mps[index], arrays.velocity_v_mps[index])
        if isinstance(arrays, AdaptiveNormalizedArrays):
            wet = arrays.active_mask & (arrays.depth_time_m[index] > DISPLAY_DRY_THRESHOLD_M)
        else:
            stride = arrays.velocity_grid_stride
            wet = arrays.active_mask[::stride, ::stride] & (arrays.depth_time_m[index, ::stride, ::stride] > DISPLAY_DRY_THRESHOLD_M)
        peak = np.fmax(peak, np.where(wet & np.isfinite(speed), speed, np.nan))
    if isinstance(arrays, AdaptiveNormalizedArrays):
        areas = arrays.face_source_overlap_area_m2
    else:
        stride = arrays.velocity_grid_stride
        cell_areas = np.broadcast_to(arrays.grid_resolution_m, arrays.shape) ** 2
        areas = np.add.reduceat(np.add.reduceat(cell_areas, np.arange(0, arrays.shape[0], stride), axis=0), np.arange(0, arrays.shape[1], stride), axis=1)
    return generate_adaptive_breaks(peak, areas, zero_epsilon=0.001)


def depth_display_range(arrays: ResultArrays) -> tuple[float, float]:
    """Return one stable colour range for every depth frame of a result."""
    values = np.asarray(arrays.max_depth_m, dtype=np.float64)
    visible = np.asarray(arrays.active_mask, dtype=bool) & np.isfinite(values)
    threshold = getattr(arrays, "display_dry_threshold_m", DISPLAY_DRY_THRESHOLD_M)
    visible &= values > threshold
    if not np.any(visible):
        return threshold, threshold
    minimum_m = float(np.min(values[visible]))
    maximum_m = float(np.max(values[visible]))
    return minimum_m, maximum_m


def depth_legend_metadata(
    minimum_m: float = DISPLAY_DRY_THRESHOLD_M,
    maximum_m: float = 1.0,
    *,
    scale: AdaptiveScaleResult | None = None,
) -> list[dict[str, Any]]:
    if not np.isfinite(minimum_m) or not np.isfinite(maximum_m) or maximum_m < minimum_m:
        raise ResultViewError("depth range is invalid")
    reference = scale or generate_adaptive_breaks(np.linspace(minimum_m, maximum_m, 100))
    return scale_legend(reference, tuple(band.rgba for band in DEPTH_BANDS))


def elevation_legend_metadata(
    minimum_m: float,
    maximum_m: float,
    *,
    scale: AdaptiveScaleResult | None = None,
) -> list[dict[str, Any]]:
    if not np.isfinite(minimum_m) or not np.isfinite(maximum_m) or maximum_m < minimum_m:
        raise ResultViewError("terrain elevation range is invalid")
    reference = scale or generate_adaptive_breaks(np.linspace(minimum_m, maximum_m, 100), anchor_zero=False)
    return scale_legend(reference, ELEVATION_COLORS)


def terrain_elevation_range(arrays: ResultArrays) -> tuple[float, float]:
    values = np.asarray(arrays.terrain_elevation_m, dtype=np.float64)
    visible = np.asarray(arrays.active_mask, dtype=bool) & np.isfinite(values)
    if not np.any(visible):
        raise ResultViewError("terrain elevation has no finite active values")
    return float(np.min(values[visible])), float(np.max(values[visible]))


def load_normalized_arrays(path: str | Path, *, static_layer: Literal["elevation", "grid_resolution"] | None = None) -> ResultArrays:
    source = Path(path)
    if not source.is_file():
        raise ResultArtifactMissing(f"normalized result file is missing: {source.name}")
    try:
        with np.load(source, allow_pickle=False) as archive:
            storage_kind = (
                str(np.asarray(archive["storage_kind"]).item())
                if "storage_kind" in archive.files
                else "regular_dense"
            )
            active = np.asarray(archive["active_mask"], dtype=bool)
            terrain = (np.asarray(archive["terrain_elevation_m"], dtype=np.float32)
                       if static_layer != "grid_resolution" else np.zeros(active.shape, dtype=np.float32))
            depth = (np.asarray(archive["depth_time_m"], dtype=np.float32)
                     if static_layer is None else np.zeros((0, *active.shape), dtype=np.float32))
            max_depth = (np.asarray(archive["max_depth_m"], dtype=np.float32)
                         if static_layer is None else np.zeros(active.shape, dtype=np.float32))
            time_values = (tuple(str(value) for value in np.asarray(archive["time_values"]).tolist())
                           if static_layer is None else ())
            has_u = static_layer is None and "velocity_u_mps" in archive.files
            has_v = static_layer is None and "velocity_v_mps" in archive.files
            if has_u != has_v:
                raise ResultViewError("normalized velocity arrays must contain both u and v")
            velocity_u = np.asarray(archive["velocity_u_mps"], dtype=np.float32) if has_u else None
            velocity_v = np.asarray(archive["velocity_v_mps"], dtype=np.float32) if has_v else None
            velocity_grid_stride = int(
                np.asarray(archive["velocity_grid_stride"]).item()
            ) if "velocity_grid_stride" in archive.files else 1

            if storage_kind == "quadtree_faces":
                resolution = np.asarray(archive["face_resolution_m"], dtype=np.int16)
                rows = np.asarray(archive["face_row_index"], dtype=np.int32)
                cols = np.asarray(archive["face_col_index"], dtype=np.int32)
                overlap = np.asarray(
                    archive["face_source_overlap_area_m2"], dtype=np.float64
                )
                source_height = int(
                    np.asarray(archive["source_height_cells"]).item()
                )
                source_width = int(
                    np.asarray(archive["source_width_cells"]).item()
                )
            elif storage_kind == "regular_dense":
                raw_resolution = np.asarray(
                    archive["grid_resolution_m"], dtype=np.float32
                )
            else:
                raise ResultViewError(
                    f"normalized result storage kind is unsupported: {storage_kind}"
                )
    except ResultViewError:
        raise
    except (KeyError, OSError, ValueError) as exc:
        raise ResultViewError("normalized result file is invalid") from exc

    if depth.shape[0] != len(time_values):
        raise ResultViewError("normalized result time axis is inconsistent")

    if storage_kind == "quadtree_faces":
        if depth.ndim != 2 or max_depth.ndim != 1 or terrain.ndim != 1 or active.ndim != 1:
            raise ResultViewError("Adaptive normalized result arrays have invalid dimensions")
        face_count = max_depth.size
        if (
            depth.shape[1] != face_count
            or terrain.size != face_count
            or active.size != face_count
        ):
            raise ResultViewError("Adaptive normalized face arrays are inconsistent")
        if any(
            values.shape != (face_count,)
            for values in (resolution, rows, cols, overlap)
        ):
            raise ResultViewError("Adaptive normalized face layout is inconsistent")
        if source_height <= 0 or source_width <= 0:
            raise ResultViewError("Adaptive normalized source dimensions are invalid")
        if np.any(resolution <= 0) or np.any(rows < 0) or np.any(cols < 0):
            raise ResultViewError("Adaptive normalized face layout contains invalid indices")
        if velocity_u is not None and (
            velocity_u.shape != depth.shape
            or velocity_v is None
            or velocity_v.shape != depth.shape
        ):
            raise ResultViewError(
                "Adaptive normalized velocity face/time shape is inconsistent"
            )
        return AdaptiveNormalizedArrays(
            depth_time_m=depth,
            max_depth_m=max_depth,
            terrain_elevation_m=terrain,
            active_mask=active,
            time_values=time_values,
            face_resolution_m=resolution,
            face_row_index=rows,
            face_col_index=cols,
            face_source_overlap_area_m2=overlap,
            source_height_cells=source_height,
            source_width_cells=source_width,
            velocity_u_mps=velocity_u,
            velocity_v_mps=velocity_v,
        )

    if depth.ndim != 3 or max_depth.ndim != 2 or terrain.ndim != 2 or active.ndim != 2:
        raise ResultViewError("normalized result arrays have invalid dimensions")
    if (
        depth.shape[1:] != max_depth.shape
        or terrain.shape != max_depth.shape
        or active.shape != max_depth.shape
    ):
        raise ResultViewError("normalized result grid shapes are inconsistent")
    sampled_shape = (
        depth.shape[0],
        math.ceil(depth.shape[1] / velocity_grid_stride),
        math.ceil(depth.shape[2] / velocity_grid_stride),
    )
    if velocity_grid_stride < 1 or (
        velocity_u is not None
        and (
            velocity_u.shape != sampled_shape
            or velocity_v is None
            or velocity_v.shape != sampled_shape
        )
    ):
        raise ResultViewError("normalized velocity grid/time shape is inconsistent")

    if raw_resolution.ndim == 0:
        regular_resolution: np.ndarray | float = float(raw_resolution)
    elif raw_resolution.shape == max_depth.shape:
        regular_resolution = raw_resolution
    else:
        raise ResultViewError("normalized grid-resolution shape is inconsistent")

    return NormalizedArrays(
        depth_time_m=depth,
        max_depth_m=max_depth,
        terrain_elevation_m=terrain,
        active_mask=active,
        time_values=time_values,
        grid_resolution_m=regular_resolution,
        velocity_u_mps=velocity_u,
        velocity_v_mps=velocity_v,
        velocity_grid_stride=velocity_grid_stride,
    )


def _clamped_max_px(max_px: int) -> int:
    return max(MIN_RENDER_PX, min(MAX_RENDER_PX, int(max_px)))


def _resize_png(image: Image.Image, *, max_px: int, categorical: bool) -> Image.Image:
    limit = _clamped_max_px(max_px)
    width, height = image.size
    longest = max(width, height)
    if longest <= limit:
        return image
    scale = limit / float(longest)
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    resampling = Image.Resampling.NEAREST if categorical else Image.Resampling.BILINEAR
    return image.resize(target, resample=resampling)


def _png_bytes(rgba: np.ndarray, *, max_px: int, categorical: bool) -> bytes:
    # Normalized arrays use row 0 as the southern edge. PNG row 0 must be north.
    north_up = np.flipud(rgba)
    image = Image.fromarray(north_up, mode="RGBA")
    image = _resize_png(image, max_px=max_px, categorical=categorical)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()


def _depth_rgba(
    values: np.ndarray,
    active_mask: np.ndarray,
    *,
    minimum_m: float,
    maximum_m: float,
    scale: AdaptiveScaleResult | None = None,
    dry_threshold_m: float = DISPLAY_DRY_THRESHOLD_M,
) -> np.ndarray:
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    visible = active_mask & np.isfinite(values) & (values > dry_threshold_m)
    reference = scale or generate_adaptive_breaks(values[visible], zero_epsilon=dry_threshold_m)
    indices = color_indices(values, reference)
    for index, band in enumerate(DEPTH_BANDS[:reference.class_count]):
        rgba[visible & (indices == index)] = band.rgba
    return rgba



def _adaptive_face_bounds(
    arrays: AdaptiveNormalizedArrays,
    index: int,
) -> tuple[int, int, int, int]:
    size = int(arrays.face_resolution_m[index])
    row0 = int(arrays.face_row_index[index]) * size
    col0 = int(arrays.face_col_index[index]) * size
    row1 = min(row0 + size, arrays.source_height_cells)
    col1 = min(col0 + size, arrays.source_width_cells)
    return row0, row1, col0, col1


def _adaptive_depth_rgba(
    arrays: AdaptiveNormalizedArrays,
    values: np.ndarray,
    *,
    minimum_m: float,
    maximum_m: float,
    scale: AdaptiveScaleResult | None = None,
) -> np.ndarray:
    if values.shape != arrays.max_depth_m.shape:
        raise ResultViewError("Adaptive face depth shape is inconsistent")
    rgba = np.zeros((*arrays.shape, 4), dtype=np.uint8)
    reference = scale or arrays.depth_scale
    indices = color_indices(values, reference)
    for face_index, value in enumerate(values):
        if not arrays.active_mask[face_index] or not np.isfinite(value):
            continue
        depth = float(value)
        if depth <= DISPLAY_DRY_THRESHOLD_M:
            continue
        band_color = DEPTH_BANDS[int(indices[face_index])].rgba
        row0, row1, col0, col1 = _adaptive_face_bounds(arrays, face_index)
        if row1 > row0 and col1 > col0:
            rgba[row0:row1, col0:col1] = band_color
    return rgba


def _adaptive_resolution_rgba(arrays: AdaptiveNormalizedArrays) -> np.ndarray:
    rgba = np.zeros((*arrays.shape, 4), dtype=np.uint8)
    face_bounds: list[tuple[int, int, int, int]] = []
    for index, resolution in enumerate(arrays.face_resolution_m):
        if not arrays.active_mask[index]:
            continue
        color = GRID_RESOLUTION_COLORS.get(int(resolution))
        if color is None:
            continue
        row0, row1, col0, col1 = _adaptive_face_bounds(arrays, index)
        if row1 > row0 and col1 > col0:
            rgba[row0:row1, col0:col1] = color
            face_bounds.append((row0, row1, col0, col1))

    # Source pixels already represent 1 m faces exactly. For coarser faces,
    # make the actual hydraulic face perimeter explicit without expanding the
    # full raster into a higher-resolution debug image.
    boundary_color = np.asarray((20, 27, 36, 245), dtype=np.uint8)
    for row0, row1, col0, col1 in face_bounds:
        if row1 - row0 <= 1 and col1 - col0 <= 1:
            continue
        # Top + left edges are sufficient to expose the face lattice while
        # retaining at least one categorical-color pixel even for a 2 m face.
        rgba[row0, col0:col1] = boundary_color
        rgba[row0:row1, col0] = boundary_color
    return rgba


def render_max_depth_png(arrays: ResultArrays, *, max_px: int = MAX_RENDER_PX) -> bytes:
    minimum_m, maximum_m = depth_display_range(arrays)
    rgba = (
        _adaptive_depth_rgba(arrays, arrays.max_depth_m, minimum_m=minimum_m, maximum_m=maximum_m, scale=arrays.depth_scale)
        if isinstance(arrays, AdaptiveNormalizedArrays)
        else _depth_rgba(arrays.max_depth_m, arrays.active_mask, minimum_m=minimum_m, maximum_m=maximum_m, scale=arrays.depth_scale, dry_threshold_m=arrays.display_dry_threshold_m)
    )
    return _png_bytes(rgba, max_px=max_px, categorical=True)


def render_depth_values_png(values: np.ndarray, active_mask: np.ndarray,
                            scale: AdaptiveScaleResult, *, max_px: int = MAX_RENDER_PX,
                            dry_threshold_m: float = DISPLAY_DRY_THRESHOLD_M) -> bytes:
    rgba = _depth_rgba(values, active_mask, minimum_m=scale.breaks[0], maximum_m=scale.maximum,
                      scale=scale, dry_threshold_m=dry_threshold_m)
    return _png_bytes(rgba, max_px=max_px, categorical=True)


def render_time_depth_png(
    arrays: ResultArrays,
    *,
    time_index: int,
    max_px: int = MAX_RENDER_PX,
) -> bytes:
    if time_index < 0 or time_index >= arrays.depth_time_m.shape[0]:
        raise ResultTimeIndexInvalid(f"time_index {time_index} is outside available output")
    values = arrays.depth_time_m[time_index]
    minimum_m, maximum_m = depth_display_range(arrays)
    rgba = (
        _adaptive_depth_rgba(arrays, values, minimum_m=minimum_m, maximum_m=maximum_m, scale=arrays.depth_scale)
        if isinstance(arrays, AdaptiveNormalizedArrays)
        else _depth_rgba(values, arrays.active_mask, minimum_m=minimum_m, maximum_m=maximum_m, scale=arrays.depth_scale, dry_threshold_m=arrays.display_dry_threshold_m)
    )
    return _png_bytes(rgba, max_px=max_px, categorical=True)


def _grid_resolution_values(arrays: NormalizedArrays) -> np.ndarray:
    if isinstance(arrays.grid_resolution_m, float):
        return np.full(arrays.shape, arrays.grid_resolution_m, dtype=np.float32)
    return np.asarray(arrays.grid_resolution_m, dtype=np.float32)


def render_grid_resolution_png(
    arrays: ResultArrays,
    *,
    max_px: int = MAX_RENDER_PX,
) -> bytes:
    if isinstance(arrays, AdaptiveNormalizedArrays):
        rgba = _adaptive_resolution_rgba(arrays)
    else:
        values = _grid_resolution_values(arrays)
        rgba = np.zeros((*arrays.shape, 4), dtype=np.uint8)
        for level, color in GRID_RESOLUTION_COLORS.items():
            rgba[arrays.active_mask & np.isclose(values, float(level))] = color
    return _png_bytes(rgba, max_px=max_px, categorical=True)


def render_regular_grid_resolution_png(
    active_mask: np.ndarray, resolution_m: float, *, max_px: int = MAX_RENDER_PX,
) -> bytes:
    rgba = np.zeros((*active_mask.shape, 4), dtype=np.uint8)
    for level, color in GRID_RESOLUTION_COLORS.items():
        if np.isclose(resolution_m, float(level)):
            rgba[active_mask] = color
    return _png_bytes(rgba, max_px=max_px, categorical=True)


def render_terrain_elevation_png(
    arrays: ResultArrays,
    *,
    max_px: int = MAX_RENDER_PX,
) -> bytes:
    indices = color_indices(arrays.terrain_elevation_m, arrays.elevation_scale)
    if isinstance(arrays, AdaptiveNormalizedArrays):
        rgba = np.zeros((*arrays.shape, 4), dtype=np.uint8)
        for index, elevation in enumerate(arrays.terrain_elevation_m):
            if not arrays.active_mask[index] or not np.isfinite(elevation):
                continue
            row0, row1, col0, col1 = _adaptive_face_bounds(arrays, index)
            rgba[row0:row1, col0:col1] = ELEVATION_COLORS[int(indices[index])]
        return _png_bytes(rgba, max_px=max_px, categorical=True)
    return render_elevation_values_png(
        arrays.terrain_elevation_m,
        arrays.active_mask,
        max_px=max_px,
    )


def render_elevation_values_png(
    terrain_elevation_m: np.ndarray,
    active_mask: np.ndarray,
    *,
    max_px: int = MAX_RENDER_PX,
) -> bytes:
    values = np.asarray(terrain_elevation_m, dtype=np.float32)
    active = np.asarray(active_mask, dtype=bool)
    if values.ndim != 2 or active.shape != values.shape:
        raise ResultViewError("elevation preview arrays must be matching 2D grids")
    visible = active & np.isfinite(values)
    if not np.any(visible):
        raise ResultViewError("terrain elevation has no finite active values")
    reference = generate_adaptive_breaks(values[visible], anchor_zero=False)
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    indices = color_indices(values, reference)
    for index, color in enumerate(ELEVATION_COLORS[:reference.class_count]):
        rgba[visible & (indices == index)] = color
    return _png_bytes(rgba, max_px=max_px, categorical=True)



def _display_arrow_length_m(*, sample_span_m: float) -> float:
    """Preserve the 0.8 arrow-length/vector-spacing ratio after decimation."""

    if sample_span_m <= 0:
        raise ResultViewError("sample_span_m must be positive")
    return 0.8 * float(sample_span_m)


def _adaptive_flow_vectors_geojson(
    arrays: AdaptiveNormalizedArrays,
    *,
    area: AnalysisArea,
    time_index: int,
    max_vectors: int,
    min_speed_mps: float,
) -> dict[str, Any]:
    if time_index < 0 or time_index >= arrays.depth_time_m.shape[0]:
        raise ResultTimeIndexInvalid(
            f"time_index {time_index} is outside available output"
        )
    if arrays.velocity_u_mps is None or arrays.velocity_v_mps is None:
        raise ResultArtifactMissing("flow-vector output is not available for this run")
    if max_vectors <= 0:
        raise ResultViewError("max_vectors must be positive")

    depth = arrays.depth_time_m[time_index]
    u = arrays.velocity_u_mps[time_index]
    vv = arrays.velocity_v_mps[time_index]
    speed = np.hypot(u, vv)
    valid = (
        arrays.active_mask
        & np.isfinite(depth)
        & (depth >= getattr(arrays, "display_dry_threshold_m", DISPLAY_DRY_THRESHOLD_M))
        & np.isfinite(u)
        & np.isfinite(vv)
        & np.isfinite(speed)
        & (speed >= min_speed_mps)
    )
    candidates = np.flatnonzero(valid)
    if candidates.size > max_vectors:
        # Preserve spatial coverage instead of taking the globally fastest
        # faces. Global top-speed ranking clusters the retained source vectors,
        # so client-side screen-space resampling cannot reveal more arrows when
        # the user zooms into an Adaptive result.
        candidate_rows = arrays.face_row_index[candidates].astype(np.float64)
        candidate_cols = arrays.face_col_index[candidates].astype(np.float64)
        candidate_span = arrays.face_resolution_m[candidates].astype(np.float64)
        center_rows = candidate_rows + 0.5 * candidate_span
        center_cols = candidate_cols + 0.5 * candidate_span
        bins_per_axis = max(1, int(np.sqrt(max_vectors)))
        row_bins = np.minimum(
            bins_per_axis - 1,
            (center_rows * bins_per_axis / max(1, arrays.source_height_cells)).astype(np.int64),
        )
        col_bins = np.minimum(
            bins_per_axis - 1,
            (center_cols * bins_per_axis / max(1, arrays.source_width_cells)).astype(np.int64),
        )
        bin_ids = row_bins * bins_per_axis + col_bins
        order = np.lexsort((-speed[candidates], bin_ids))
        sorted_bins = bin_ids[order]
        first = np.r_[True, sorted_bins[1:] != sorted_bins[:-1]]
        candidates = candidates[order[first]]

    transformer = Transformer.from_crs(
        local_crs(area),
        CRS.from_epsg(4326),
        always_xy=True,
    )
    xmin = -area.width_m / 2.0
    ymin = -area.height_m / 2.0
    features: list[dict[str, Any]] = []

    for raw_index in candidates:
        index = int(raw_index)
        row0, row1, col0, col1 = _adaptive_face_bounds(arrays, index)
        if row1 <= row0 or col1 <= col0:
            continue
        sample_u = float(u[index])
        sample_v = float(vv[index])
        sample_speed = float(speed[index])
        direction_x = sample_u / sample_speed
        direction_y = sample_v / sample_speed
        center_x = xmin + 0.5 * (col0 + col1)
        center_y = ymin + 0.5 * (row0 + row1)
        face_span_m = float(min(row1 - row0, col1 - col0))
        arrow_length_m = _display_arrow_length_m(sample_span_m=face_span_m)
        tail_scale = arrow_length_m * 0.42
        tip_scale = arrow_length_m * 0.58
        tail = (
            center_x - direction_x * tail_scale,
            center_y - direction_y * tail_scale,
        )
        tip = (
            center_x + direction_x * tip_scale,
            center_y + direction_y * tip_scale,
        )
        head_length = arrow_length_m * 0.28
        head_angle = np.deg2rad(30.0)
        cos_a = float(np.cos(head_angle))
        sin_a = float(np.sin(head_angle))
        back_x = -direction_x
        back_y = -direction_y
        left_dir = (
            back_x * cos_a - back_y * sin_a,
            back_x * sin_a + back_y * cos_a,
        )
        right_dir = (
            back_x * cos_a + back_y * sin_a,
            -back_x * sin_a + back_y * cos_a,
        )
        left = (
            tip[0] + left_dir[0] * head_length,
            tip[1] + left_dir[1] * head_length,
        )
        right = (
            tip[0] + right_dir[0] * head_length,
            tip[1] + right_dir[1] * head_length,
        )

        def lonlat(point: tuple[float, float]) -> list[float]:
            lon, lat = transformer.transform(point[0], point[1])
            return [float(lon), float(lat)]

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [
                        [lonlat(tail), lonlat(tip)],
                        [lonlat(tip), lonlat(left)],
                        [lonlat(tip), lonlat(right)],
                    ],
                },
                "properties": {
                    "speed_mps": sample_speed,
                    "u_mps": sample_u,
                    "v_mps": sample_v,
                    "time_index": time_index,
                    "face_index": index,
                    "grid_resolution_m": float(arrays.face_resolution_m[index]),
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "speed_unit": "m/s",
            "min_speed_mps": float(min_speed_mps),
            "arrow_count": len(features),
            "sampling_method": "native-quadtree-spatial-fastest-per-bin",
        },
    }


def flow_vectors_geojson(
    arrays: ResultArrays,
    *,
    area: AnalysisArea,
    time_index: int,
    max_vectors: int = 900,
    min_speed_mps: float = 0.001,
) -> dict[str, Any]:
    """Return sampled flow arrows as vector GeoJSON with speed metadata."""
    if isinstance(arrays, AdaptiveNormalizedArrays):
        return _adaptive_flow_vectors_geojson(
            arrays,
            area=area,
            time_index=time_index,
            max_vectors=max_vectors,
            min_speed_mps=min_speed_mps,
        )
    if time_index < 0 or time_index >= arrays.depth_time_m.shape[0]:
        raise ResultTimeIndexInvalid(f"time_index {time_index} is outside available output")
    if arrays.velocity_u_mps is None or arrays.velocity_v_mps is None:
        raise ResultArtifactMissing("flow-vector output is not available for this run")
    if max_vectors <= 0:
        raise ResultViewError("max_vectors must be positive")

    height, width = arrays.shape
    target_stride = int(np.ceil(np.sqrt((height * width) / float(max_vectors))))
    stride = max(4, target_stride)
    depth = arrays.depth_time_m[time_index]
    u = arrays.velocity_u_mps[time_index]
    v = arrays.velocity_v_mps[time_index]
    cell_width_m = area.width_m / float(width)
    cell_height_m = area.height_m / float(height)
    xmin = -area.width_m / 2.0
    ymin = -area.height_m / 2.0
    transformer = Transformer.from_crs(
        local_crs(area),
        CRS.from_epsg(4326),
        always_xy=True,
    )

    features: list[dict[str, Any]] = []
    for row0 in range(0, height, stride):
        row1 = min(height, row0 + stride)
        for col0 in range(0, width, stride):
            col1 = min(width, col0 + stride)
            wet = (
                arrays.active_mask[row0:row1, col0:col1]
                & np.isfinite(depth[row0:row1, col0:col1])
                & (depth[row0:row1, col0:col1] >= DISPLAY_DRY_THRESHOLD_M)
                & np.isfinite(u[row0:row1, col0:col1])
                & np.isfinite(v[row0:row1, col0:col1])
            )
            if not np.any(wet):
                continue

            u_block = u[row0:row1, col0:col1]
            v_block = v[row0:row1, col0:col1]
            speed_block = np.hypot(u_block, v_block)
            valid_flow = wet & np.isfinite(speed_block) & (speed_block >= min_speed_mps)
            if not np.any(valid_flow):
                continue

            scored = np.where(valid_flow, speed_block, -np.inf)
            local_row, local_col = np.unravel_index(int(np.argmax(scored)), scored.shape)
            sample_u = float(u_block[local_row, local_col])
            sample_v = float(v_block[local_row, local_col])
            speed = float(speed_block[local_row, local_col])
            row = row0 + int(local_row)
            column = col0 + int(local_col)

            direction_x = sample_u / speed
            direction_y = sample_v / speed
            center_x = xmin + (column + 0.5) * cell_width_m
            center_y = ymin + (row + 0.5) * cell_height_m
            block_span_m = min(
                max(cell_width_m, 1e-6) * (col1 - col0),
                max(cell_height_m, 1e-6) * (row1 - row0),
            )
            arrow_length_m = _display_arrow_length_m(sample_span_m=block_span_m)
            tail_scale = arrow_length_m * 0.42
            tip_scale = arrow_length_m * 0.58

            tail = (
                center_x - direction_x * tail_scale,
                center_y - direction_y * tail_scale,
            )
            tip = (
                center_x + direction_x * tip_scale,
                center_y + direction_y * tip_scale,
            )
            head_length = arrow_length_m * 0.28
            head_angle = np.deg2rad(30.0)
            cos_a = float(np.cos(head_angle))
            sin_a = float(np.sin(head_angle))
            back_x = -direction_x
            back_y = -direction_y
            left_dir = (
                back_x * cos_a - back_y * sin_a,
                back_x * sin_a + back_y * cos_a,
            )
            right_dir = (
                back_x * cos_a + back_y * sin_a,
                -back_x * sin_a + back_y * cos_a,
            )
            left = (
                tip[0] + left_dir[0] * head_length,
                tip[1] + left_dir[1] * head_length,
            )
            right = (
                tip[0] + right_dir[0] * head_length,
                tip[1] + right_dir[1] * head_length,
            )

            def lonlat(point: tuple[float, float]) -> list[float]:
                lon, lat = transformer.transform(point[0], point[1])
                return [float(lon), float(lat)]

            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [
                            [lonlat(tail), lonlat(tip)],
                            [lonlat(tip), lonlat(left)],
                            [lonlat(tip), lonlat(right)],
                        ],
                    },
                    "properties": {
                        "speed_mps": speed,
                        "u_mps": sample_u,
                        "v_mps": sample_v,
                        "time_index": time_index,
                        "row": row,
                        "column": column,
                    },
                }
            )

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "speed_unit": "m/s",
            "min_speed_mps": float(min_speed_mps),
            "sample_stride_cells": stride,
            "arrow_count": len(features),
            "sampling_method": "max-speed-wet-cell-per-block",
        },
    }


def inspect_native_point(
    arrays: ResultArrays,
    *,
    area: AnalysisArea,
    lon_deg: float,
    lat_deg: float,
    time_index: int | None = None,
) -> dict[str, Any]:
    if time_index is not None and (time_index < 0 or time_index >= arrays.depth_time_m.shape[0]):
        raise ResultTimeIndexInvalid(f"time_index {time_index} is outside available output")

    transformer = Transformer.from_crs(
        CRS.from_epsg(4326),
        local_crs(area),
        always_xy=True,
    )
    x_m, y_m = transformer.transform(lon_deg, lat_deg)
    xmin = -area.width_m / 2.0
    ymin = -area.height_m / 2.0
    xmax = area.width_m / 2.0
    ymax = area.height_m / 2.0
    if not (xmin <= x_m < xmax and ymin <= y_m < ymax):
        raise PointOutsideResult("point is outside result bounds")

    height, width = arrays.shape
    row = int(np.floor((y_m - ymin) * height / area.height_m))
    col = int(np.floor((x_m - xmin) * width / area.width_m))
    if row < 0 or row >= height or col < 0 or col >= width:
        raise PointOutsideResult("point is outside result grid")

    time_value = arrays.time_values[time_index] if time_index is not None else None
    if isinstance(arrays, AdaptiveNormalizedArrays):
        sizes = arrays.face_resolution_m.astype(np.int64)
        row0 = arrays.face_row_index.astype(np.int64) * sizes
        col0 = arrays.face_col_index.astype(np.int64) * sizes
        row1 = np.minimum(row0 + sizes, arrays.source_height_cells)
        col1 = np.minimum(col0 + sizes, arrays.source_width_cells)
        matches = np.flatnonzero(
            (row >= row0) & (row < row1) & (col >= col0) & (col < col1)
        )
        if matches.size != 1:
            raise ResultViewError("Adaptive point does not resolve to exactly one face")
        face = int(matches[0])
        if not bool(arrays.active_mask[face]):
            return {
                "lon_deg": lon_deg,
                "lat_deg": lat_deg,
                "has_data": False,
                "row": row,
                "column": col,
                "time_index": time_index,
                "time_value": time_value,
                "depth_m": None,
                "max_depth_m": None,
                "max_time_index": None,
                "max_time_value": None,
                "terrain_elevation_m": None,
                "grid_resolution_m": None,
            }
        depth_series = arrays.depth_time_m[:, face]
        max_time_index = int(np.nanargmax(depth_series))
        return {
            "lon_deg": lon_deg,
            "lat_deg": lat_deg,
            "has_data": True,
            "row": row,
            "column": col,
            "time_index": time_index,
            "time_value": time_value,
            "depth_m": (
                float(depth_series[time_index])
                if time_index is not None
                else None
            ),
            "max_depth_m": float(arrays.max_depth_m[face]),
            "max_time_index": max_time_index,
            "max_time_value": arrays.time_values[max_time_index],
            "terrain_elevation_m": float(arrays.terrain_elevation_m[face]),
            "grid_resolution_m": float(arrays.face_resolution_m[face]),
        }

    if not bool(arrays.active_mask[row, col]):
        return {
            "lon_deg": lon_deg,
            "lat_deg": lat_deg,
            "has_data": False,
            "row": row,
            "column": col,
            "time_index": time_index,
            "time_value": time_value,
            "depth_m": None,
            "max_depth_m": None,
            "max_time_index": None,
            "max_time_value": None,
            "terrain_elevation_m": None,
            "grid_resolution_m": None,
        }

    depth_series = arrays.depth_time_m[:, row, col]
    max_time_index = int(np.argmax(depth_series))
    max_time_value = arrays.time_values[max_time_index]
    depth_m = (
        float(depth_series[time_index])
        if time_index is not None
        else None
    )
    resolution_values = _grid_resolution_values(arrays)
    return {
        "lon_deg": lon_deg,
        "lat_deg": lat_deg,
        "has_data": True,
        "row": row,
        "column": col,
        "time_index": time_index,
        "time_value": time_value,
        "depth_m": depth_m,
        "max_depth_m": float(arrays.max_depth_m[row, col]),
        "max_time_index": max_time_index,
        "max_time_value": max_time_value,
        "terrain_elevation_m": float(arrays.terrain_elevation_m[row, col]),
        "grid_resolution_m": float(resolution_values[row, col]),
    }
