"""Initial water and face fluxes for SFINCS 2.4.0 regular-grid type-1 restart.

Layout follows the pinned Galibier sfincs_domain/initial_conditions/output
sources: Fortran cell order, east then north face per active cell, q dummy,
and boundary-only uvmean. The stock engine evolves this state thereafter.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from pyproj import Transformer

from floodsim.domain.water_magic import MagicTimeSeries, WaterMagicConfig
from floodsim.preprocessing.full_grid import FullGridProduct
from floodsim.sfincs.water_magic_forcing import MagicForcingError, magic_cell_rates


def initial_velocity(
    magic: WaterMagicConfig, x: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Local offsets from the magic center; clockwise north points east."""
    speed = magic.initial_speed_mps
    if magic.initial_motion == "none":
        return np.zeros_like(x), np.zeros_like(y)
    if magic.initial_motion == "directional":
        angle = np.radians(magic.bearing_deg)
        return np.full_like(x, speed * np.sin(angle)), np.full_like(y, speed * np.cos(angle))
    radius = np.hypot(x, y)
    inverse = np.divide(1.0, radius, out=np.zeros_like(radius), where=radius > 0)
    if magic.initial_motion == "radial":
        return speed * x * inverse, speed * y * inverse
    core = magic.vortex_core_radius_m
    factor = np.minimum(radius / core, core * inverse)
    sign = 1 if magic.vortex_direction == "clockwise" else -1
    return sign * speed * factor * y * inverse, -sign * speed * factor * x * inverse


def regular_faces(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return flat Fortran indices for each east/north pair in engine order."""
    height, width = mask.shape
    flat_mask = mask.ravel(order="F")
    cells = np.flatnonzero(flat_mask > 0)
    row, col = cells % height, cells // height
    neighbors = np.column_stack((cells + height, cells + 1))
    inside = np.column_stack((col + 1 < width, row + 1 < height))
    active = inside & (flat_mask[np.minimum(neighbors, flat_mask.size - 1)] > 0)
    return (np.repeat(cells, 2)[active.ravel()], neighbors[active],
            np.tile([0, 1], cells.size)[active.ravel()])


def _write_record(handle, values: np.ndarray) -> None:
    payload = values.tobytes()
    marker = struct.pack("<i", len(payload))
    handle.write(marker)
    handle.write(payload)
    handle.write(marker)


def write_magic_initial_state(root: Path, source: MagicTimeSeries, grid: FullGridProduct) -> dict:
    """Use the written model's cell order/elevation; never add src/dis water."""
    magic = source.config
    rates, report = magic_cell_rates(source, grid)
    active = np.flatnonzero((grid.sfincs_mask > 0).ravel(order="F"))
    indices = np.fromfile(root / "sfincs.ind", dtype="<u4")
    if indices.size != active.size + 1 or indices[0] != active.size or not np.array_equal(indices[1:], active + 1):
        raise MagicForcingError("初期状態とSFINCSの格子順序が一致しません。")
    mask_values = np.fromfile(root / "sfincs.msk", dtype="u1")
    expected_mask = grid.sfincs_mask.ravel(order="F")[active]
    if not np.array_equal(mask_values, expected_mask):
        raise MagicForcingError("初期状態とSFINCSの有効格子が一致しません。")
    bed = np.fromfile(root / "sfincs.dep", dtype="<f4")
    if bed.size != active.size or not np.all(np.isfinite(bed)):
        raise MagicForcingError("初期状態に必要な地形データが不正です。")
    integral = magic.casting_seconds - magic.transition_seconds / 2
    depth = rates.astype(float).ravel(order="F")[active] * integral / 3_600_000
    levels = (bed.astype(float) + depth).astype("<f4")
    represented_depth = np.maximum(levels.astype(float) - bed, 0)
    volume = float(represented_depth.sum()) * grid.dx_m * grid.dy_m
    error = abs(volume / magic.volume_m3 - 1)
    if error > 0.01:
        raise MagicForcingError("初期水量の保存精度が不足します。水量を増やすか効果範囲を小さくしてください。")

    total = grid.height_cells * grid.width_cells
    flat_bed = np.zeros(total, dtype=float)
    flat_levels = np.zeros(total, dtype=float)
    flat_depth = np.zeros(total, dtype=float)
    flat_bed[active], flat_levels[active], flat_depth[active] = bed, levels, represented_depth
    first, second, direction = regular_faces(grid.sfincs_mask)
    mask = grid.sfincs_mask.ravel(order="F")
    row, col = first % grid.height_cells, first // grid.height_cells
    x = grid.x0_m + (col + .5 + .5 * (direction == 0)) * grid.dx_m
    y = grid.y0_m + (row + .5 + .5 * (direction == 1)) * grid.dy_m
    transform = Transformer.from_crs("EPSG:4326", grid.crs_wkt, always_xy=True)
    cx, cy = transform.transform(magic.position.lon_deg, magic.position.lat_deg)
    u, v = initial_velocity(magic, x - cx, y - cy)
    face_speed = np.where(direction == 0, u, v)
    surface = np.maximum(flat_levels[first], flat_levels[second])
    face_depth = np.maximum(surface - .5 * (flat_bed[first] + flat_bed[second]), 1e-6)
    # No impulse through buildings, dry shore faces, or prescribed/outflow boundaries.
    wet = (flat_depth[first] > 1e-6) & (flat_depth[second] > 1e-6)
    wet &= surface > np.maximum(flat_bed[first], flat_bed[second]) + 1e-6
    wet &= (mask[first] == 1) & (mask[second] == 1)
    q = np.zeros(first.size + 1, dtype="<f4")
    q[:-1] = np.where(wet, face_depth * face_speed, 0)
    boundary = ((mask[first] == 1) & np.isin(mask[second], [2, 3, 5, 6])) | (
        (mask[second] == 1) & np.isin(mask[first], [2, 3, 5, 6]))
    mean_boundary_velocity = np.zeros(np.count_nonzero(boundary), dtype="<f4")
    path = root / "water_magic_initial.rst"
    with path.open("wb") as handle:
        for values in (np.array([1], dtype="<i4"), levels, q, mean_boundary_velocity):
            _write_record(handle, values)
    report.update(
        source_profile="instant_initial_state", transition_seconds=0,
        serialized_volume_m3=volume, relative_volume_error=error,
        initial_state_file=path.name, initial_motion=magic.initial_motion,
        initial_speed_mps=magic.initial_speed_mps,
        active_cell_count=int(active.size), face_count=int(first.size),
        initialized_moving_faces=int(np.count_nonzero(q)),
        maximum_face_speed_mps=float(np.max(np.abs(np.where(wet, face_speed, 0)), initial=0)),
        engine_forcing="type-1 initial restart; no src/dis or precipitation",
        source_point_count=0,
    )
    return report
