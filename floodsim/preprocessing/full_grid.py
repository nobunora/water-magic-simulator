"""Uniform hydraulic grid preparation."""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import numpy as np
from rasterio.features import rasterize  # type: ignore[import-untyped]
from rasterio.transform import from_origin  # type: ignore[import-untyped]
from shapely.errors import GEOSException  # type: ignore[import-untyped]
from shapely.geometry import LineString, Polygon  # type: ignore[import-untyped]

from floodsim.domain.geometry import AnalysisArea
from floodsim.preprocessing.roof_rainfall import (
    RoofRainAllocation,
    allocate_roof_rainfall,
)
from floodsim.providers.common import local_crs
from floodsim.providers.gsi_elevation import ElevationProduct

GENERAL_MANNING = 0.050
ROAD_MANNING = 0.030
BUILDING_PERIMETER_MANNING = 0.060
OUTFLOW_MASK = np.uint8(3)
NEUMANN_MASK = np.uint8(6)


@dataclass(frozen=True)
class FullGridProduct:
    elevation_m: np.ndarray
    building_mask: np.ndarray
    sfincs_mask: np.ndarray
    manning_n: np.ndarray
    rain_weight: np.ndarray
    roof_allocation: RoofRainAllocation
    width_cells: int
    height_cells: int
    dx_m: float
    dy_m: float
    x0_m: float
    y0_m: float
    crs_wkt: str
    road_mask: np.ndarray | None = None
    adaptive_hard_boundary_zone: np.ndarray | None = None
    adaptive_resolution_ceiling_m: np.ndarray | None = None
    native_structure_mask: np.ndarray | None = None

    @property
    def cell_count(self) -> int:
        return self.width_cells * self.height_cells


def _cell_count(size_m: float, grid_m: float) -> int:
    count = size_m / grid_m
    rounded = round(count)
    if rounded <= 0 or not math.isclose(count, rounded, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("analysis dimensions must be divisible by the uniform grid size")
    return rounded


def _building_perimeter_mask(building_mask: np.ndarray) -> np.ndarray:
    """Return the eight-connected active-cell ring immediately around buildings."""
    perimeter = np.zeros_like(building_mask, dtype=bool)
    perimeter[1:, :] |= building_mask[:-1, :]
    perimeter[:-1, :] |= building_mask[1:, :]
    perimeter[:, 1:] |= building_mask[:, :-1]
    perimeter[:, :-1] |= building_mask[:, 1:]
    perimeter[1:, 1:] |= building_mask[:-1, :-1]
    perimeter[1:, :-1] |= building_mask[:-1, 1:]
    perimeter[:-1, 1:] |= building_mask[1:, :-1]
    perimeter[:-1, :-1] |= building_mask[1:, 1:]
    return perimeter & ~building_mask


def _cell_center_elevation(product: ElevationProduct, height: int, width: int) -> np.ndarray:
    z = np.asarray(product.z, dtype=np.float32)
    if z.shape == (height, width):
        out = z.copy()
    elif z.shape == (height + 1, width + 1):
        out = 0.25 * (z[:-1, :-1] + z[1:, :-1] + z[:-1, 1:] + z[1:, 1:])
    else:
        raise ValueError(
            f"unexpected elevation shape {z.shape}; expected {(height, width)} or "
            f"{(height + 1, width + 1)}"
        )
    if not np.isfinite(out).all():
        raise ValueError("uniform-grid terrain contains non-finite elevations")
    return out.astype(np.float32, copy=False)


def _cell_center_uncovered_mask(
    product: ElevationProduct,
    height: int,
    width: int,
) -> np.ndarray:
    uncovered = getattr(product, "uncovered_boundary_mask", None)
    if uncovered is None:
        return np.zeros((height, width), dtype=bool)
    mask = np.asarray(uncovered, dtype=bool)
    if mask.shape == (height, width):
        return mask.copy()
    if mask.shape == (height + 1, width + 1):
        return mask[:-1, :-1] & mask[1:, :-1] & mask[:-1, 1:] & mask[1:, 1:]
    raise ValueError(
        f"unexpected uncovered boundary mask shape {mask.shape}; expected {(height, width)} "
        f"or {(height + 1, width + 1)}"
    )


def _polygon_shapes(items: list[np.ndarray]) -> list[tuple[Polygon, int]]:
    shapes: list[tuple[Polygon, int]] = []
    for coords in items:
        try:
            points = np.asarray(coords, dtype=float)
        except (TypeError, ValueError):
            continue
        if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] < 2:
            continue
        xy = points[:, :2]
        if not np.isfinite(xy).all():
            continue
        try:
            polygon = Polygon(xy)
        except (GEOSException, TypeError, ValueError):
            continue
        if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
            shapes.append((polygon, 1))
    return shapes


def _road_shapes(vectors: Any) -> list[tuple[object, int]]:
    shapes: list[tuple[object, int]] = list(_polygon_shapes(list(vectors.road_polygons)))
    for coords in vectors.road_lines:
        try:
            points = np.asarray(coords, dtype=float)
        except (TypeError, ValueError):
            continue
        if points.ndim != 2 or points.shape[0] < 2 or points.shape[1] < 2:
            continue
        xy = points[:, :2]
        if not np.isfinite(xy).all():
            continue
        try:
            line = LineString(xy)
        except (GEOSException, TypeError, ValueError):
            continue
        if line.is_valid and not line.is_empty and line.length > 0:
            shapes.append((line, 1))
    return shapes


def _rasterize_local(
    shapes: list[tuple[object, int]],
    *,
    width: int,
    height: int,
    width_m: float,
    height_m: float,
    grid_m: float,
    all_touched: bool,
) -> np.ndarray:
    if not shapes:
        return np.zeros((height, width), dtype=bool)
    transform = from_origin(-width_m / 2.0, height_m / 2.0, grid_m, grid_m)
    north_to_south = rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        default_value=1,
        all_touched=all_touched,
        dtype="uint8",
    )
    return np.flipud(north_to_south).astype(bool)


def _inset_polygon_shapes(
    shapes: list[tuple[Polygon, int]],
    *,
    inset_m: float,
) -> list[tuple[object, int]]:
    """Inset polygons and omit geometry too narrow to remain an obstacle."""
    inset_shapes: list[tuple[object, int]] = []
    for polygon, value in shapes:
        try:
            inset = polygon.buffer(-inset_m)
        except (GEOSException, TypeError, ValueError):
            continue
        if not inset.is_empty and inset.area > 0:
            inset_shapes.append((inset, value))
    return inset_shapes


def build_full_1m_grid(
    area: AnalysisArea,
    elevation: ElevationProduct,
    vectors: Any,
    *,
    grid_m: float = 1.0,
    progress_callback: Callable[[float, str], None] | None = None,
) -> FullGridProduct:
    """Create uniform hydraulic arrays required by the SFINCS builder."""
    if grid_m not in {0.5, 1.0, 2.0, 4.0}:
        raise ValueError("uniform grid size must be 0.5, 1, 2, or 4 metres")
    width = _cell_count(area.width_m, grid_m)
    height = _cell_count(area.height_m, grid_m)
    grid_label = "Full 1 m" if grid_m == 1 else f"均一 {grid_m:g} m"
    if progress_callback is not None:
        progress_callback(0.0, f"均一 {grid_m:g} m 前処理 0/3 完了 / 残り3処理")

    def build_building_mask() -> np.ndarray:
        shapes = _polygon_shapes(list(vectors.buildings))
        building_shapes = _inset_polygon_shapes(shapes, inset_m=0.5)
        return _rasterize_local(
            building_shapes,
            width=width,
            height=height,
            width_m=area.width_m,
            height_m=area.height_m,
            grid_m=grid_m,
            all_touched=False,
        )

    def build_road_mask() -> np.ndarray:
        return _rasterize_local(
            _road_shapes(vectors),
            width=width,
            height=height,
            width_m=area.width_m,
            height_m=area.height_m,
            grid_m=grid_m,
            all_touched=True,
        )

    worker_count = min(3, max(1, os.cpu_count() or 1))
    completed: dict[str, np.ndarray] = {}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="full1m-prep") as executor:
        futures = {
            executor.submit(_cell_center_elevation, elevation, height, width): "地形",
            executor.submit(build_building_mask): "建物マスク",
            executor.submit(build_road_mask): "道路マスク",
        }
        for done_count, future in enumerate(as_completed(futures), start=1):
            label_name = futures[future]
            completed[label_name] = future.result()
            if progress_callback is not None:
                progress_callback(
                    0.15 * done_count,
                    f"{grid_label}前処理 {done_count}/3 完了（{label_name}） / 残り{3 - done_count}処理",
                )

    terrain = completed["地形"]
    building_mask = completed["建物マスク"]
    road_mask = completed["道路マスク"]
    water_mask = _cell_center_uncovered_mask(elevation, height, width)
    land_mask = ~water_mask
    # Provider polygons can extend over sea or DEM-uncovered cells. Those
    # cells receive no meteorological rainfall in this model and therefore
    # must not enter the roof-runoff recipient search or mass balance.
    building_mask &= land_mask

    if progress_callback is not None:
        progress_callback(
            0.52,
            f"建物セル {int(np.count_nonzero(building_mask)):,} / 道路セル "
            f"{int(np.count_nonzero(road_mask)):,} を確定",
        )

    manning = np.full((height, width), GENERAL_MANNING, dtype=np.float32)
    manning[road_mask & ~building_mask] = ROAD_MANNING
    building_perimeter = _building_perimeter_mask(building_mask)
    manning[building_perimeter & land_mask] = BUILDING_PERIMETER_MANNING

    sfincs_mask = np.zeros((height, width), dtype=np.uint8)
    sfincs_mask[land_mask] = 1
    adjacent_water = np.zeros((height, width), dtype=bool)
    adjacent_water[1:, :] |= water_mask[:-1, :]
    adjacent_water[:-1, :] |= water_mask[1:, :]
    adjacent_water[:, 1:] |= water_mask[:, :-1]
    adjacent_water[:, :-1] |= water_mask[:, 1:]
    sfincs_mask[land_mask & adjacent_water] = OUTFLOW_MASK
    sfincs_mask[building_mask] = 0

    # The rectangular analysis limit is closed: retaining active msk=1 cells
    # preserves rainfall on the outer strip while avoiding an msk=3/6 face
    # that exchanges water with the unmodelled exterior.
    rectangular_boundary = np.zeros((height, width), dtype=bool)
    rectangular_boundary[[0, -1], :] = True
    rectangular_boundary[:, [0, -1]] = True
    sfincs_mask[rectangular_boundary & land_mask & ~building_mask] = 1

    if progress_callback is not None:
        progress_callback(0.60, "粗度・SFINCS建物マスク完了 / 屋根雨水配分を開始")

    def roof_progress(done: int, total: int) -> None:
        if progress_callback is None:
            return
        fraction = 1.0 if total == 0 else done / total
        remaining = max(0, total - done)
        progress_callback(
            0.60 + 0.35 * fraction,
            f"屋根雨水配分 {done}/{total}連結建物群"
            f"（取得ポリゴン{len(vectors.buildings)}件を1 mマスク化）"
            f" / 残り{remaining}群",
        )

    allocation = allocate_roof_rainfall(
        building_mask,
        active_mask=land_mask,
        cell_area_m2=grid_m * grid_m,
        max_distance_cells=max(1, math.ceil(5.0 / grid_m)),
        tolerance=1e-9,
        progress_callback=roof_progress,
    )
    if progress_callback is not None:
        progress_callback(1.0, f"{grid_label}格子・建物マスク・粗度の構築完了")
    crs = local_crs(area)
    return FullGridProduct(
        elevation_m=terrain,
        building_mask=building_mask,
        road_mask=road_mask,
        sfincs_mask=sfincs_mask,
        manning_n=manning,
        rain_weight=allocation.rain_weight.astype(np.float32),
        roof_allocation=allocation,
        width_cells=width,
        height_cells=height,
        dx_m=grid_m,
        dy_m=grid_m,
        x0_m=-area.width_m / 2.0,
        y0_m=-area.height_m / 2.0,
        crs_wkt=crs.to_wkt(),
        # Reuse the authoritative Full 1 m SFINCS mask as the immutable
        # Adaptive boundary-zone source. This simultaneously preserves
        # building boundaries (0/1) and the analysis-domain edge (1/6)
        # without inventing a second boundary definition.
        adaptive_hard_boundary_zone=sfincs_mask.astype(np.int32, copy=True),
    )
