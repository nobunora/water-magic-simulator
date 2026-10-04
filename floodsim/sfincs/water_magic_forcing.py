"""Resolve conservative cell-overlap water injection and engine sampling."""

from math import cos, radians, sin
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import area, box, intersection
from shapely.geometry import Point, Polygon
from shapely.ops import transform as transform_geometry

from floodsim.domain.water_magic import MagicTimeSeries, magic_output_interval
from floodsim.preprocessing.full_grid import FullGridProduct


class MagicForcingError(ValueError):
    code = "INPUT_MAGIC_INVALID"
    retryable = False


def magic_cell_rates(source: MagicTimeSeries, grid: FullGridProduct) -> tuple[np.ndarray, dict]:
    """Intersect the footprint with cells; distribute volume over eligible overlap."""
    magic = source.config
    domain = box(grid.x0_m, grid.y0_m,
                 grid.x0_m + grid.width_cells * grid.dx_m,
                 grid.y0_m + grid.height_cells * grid.dy_m)
    if magic.footprint_kind == "domain":
        footprint = domain
    else:
        transform = Transformer.from_crs("EPSG:4326", grid.crs_wkt, always_xy=True)
        cx, cy = transform.transform(magic.position.lon_deg, magic.position.lat_deg)
        bearing = radians(magic.bearing_deg)
        def offset(right: float, forward: float) -> tuple[float, float]:
            return (cx + right * cos(bearing) + forward * sin(bearing),
                    cy - right * sin(bearing) + forward * cos(bearing))
        if magic.footprint_kind == "rectangle":
            footprint = Polygon([offset(-magic.width_m / 2, 0), offset(magic.width_m / 2, 0),
                offset(magic.width_m / 2, magic.length_m), offset(-magic.width_m / 2, magic.length_m)])
        elif magic.footprint_kind == "sector" and magic.sector_angle_deg < 360:
            angles = np.linspace(-radians(magic.sector_angle_deg) / 2,
                radians(magic.sector_angle_deg) / 2, 1025)
            footprint = Polygon([(cx, cy), *[offset(magic.radius_m * sin(a), magic.radius_m * cos(a)) for a in angles]])
        else:
            footprint = Point(cx, cy).buffer(magic.radius_m, quad_segs=1024)
        if not domain.buffer(1e-6).covers(footprint):
            raise MagicForcingError("魔法の効果範囲が解析範囲の外にあります。範囲または配置を変更してください。")
    x = grid.x0_m + np.arange(grid.width_cells) * grid.dx_m
    y = grid.y0_m + np.arange(grid.height_cells) * grid.dy_m
    xmin, ymin, xmax, ymax = footprint.bounds
    columns = np.flatnonzero((x + grid.dx_m > xmin) & (x < xmax))
    rows = np.flatnonzero((y + grid.dy_m > ymin) & (y < ymax))
    cells = box(x[columns][None, :], y[rows][:, None], x[columns][None, :] + grid.dx_m, y[rows][:, None] + grid.dy_m)
    overlaps = np.zeros((grid.height_cells, grid.width_cells), dtype=float)
    overlaps[np.ix_(rows, columns)] = area(intersection(cells, footprint))
    eligible = (grid.sfincs_mask > 0) & ~grid.building_mask
    overlaps = np.where(eligible, overlaps, 0.0)
    effective_area = float(overlaps.sum())
    if effective_area <= 0:
        raise MagicForcingError("効果範囲に給水可能な地表格子がありません。配置を変更してください。")
    integral_seconds = magic.casting_seconds - magic.transition_seconds / 2
    rates = overlaps / effective_area * magic.volume_m3 * 3_600_000 / (
        integral_seconds * grid.dx_m * grid.dy_m
    )
    if magic.release_mode == "continuous" and float(rates.max()) > 10_000_000:
        raise MagicForcingError("局所給水強度が実装上限10000000 mm/hを超えます。水量を減らすか範囲を広げてください。")
    serialized = rates.astype(np.float32)
    generated_volume = float(serialized.astype(float).sum()) * grid.dx_m * grid.dy_m * integral_seconds / 3_600_000
    return serialized, {
        "configuration": magic.model_dump(mode="json"),
        "resolved_geometry": footprint.__geo_interface__,
        "geographic_geometry": transform_geometry(Transformer.from_crs(grid.crs_wkt, "EPSG:4326", always_xy=True).transform, footprint).__geo_interface__,
        "crs_wkt": grid.crs_wkt,
        "eligible_area_m2": effective_area,
        "geometric_area_m2": float(footprint.area),
        "requested_volume_m3": magic.volume_m3,
        "serialized_volume_m3": generated_volume,
        "relative_volume_error": abs(generated_volume / magic.volume_m3 - 1),
        "source_profile": "constant_then_normalized_terminal_ramp",
        "transition_seconds": magic.transition_seconds,
        "source_eligibility": "active ground only; buildings excluded; volume renormalized",
        "output_interval_seconds": magic_output_interval(magic.casting_seconds, magic.relaxation_seconds),
    }


def write_magic_discharge(root: Path, source: MagicTimeSeries, grid: FullGridProduct) -> dict:
    """Write seconds-based source points, one per intersected eligible cell."""
    rates, report = magic_cell_rates(source, grid)
    rows, columns = np.nonzero(rates)
    points = np.column_stack((grid.x0_m + (columns + 0.5) * grid.dx_m,
                              grid.y0_m + (rows + 0.5) * grid.dy_m))
    discharge = rates[rows, columns].astype(float) * grid.dx_m * grid.dy_m / 3_600_000
    np.savetxt(root / "water_magic.src", points, fmt="%.9f")
    factors = [1.0, 1.0, *([0.0] * (len(source.elapsed_seconds) - 2))]
    with (root / "water_magic.dis").open("w", encoding="ascii") as stream:
        for time, factor in zip(source.elapsed_seconds, factors, strict=True):
            stream.write(f"{time:.9f} " + " ".join(f"{flow * factor:.12e}" for flow in discharge) + "\n")
    report["source_point_count"] = len(rows)
    report["engine_forcing"] = "seconds-based src/dis; no precipitation"
    return report
