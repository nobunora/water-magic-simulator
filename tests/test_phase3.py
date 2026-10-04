from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import numpy as np
import pytest
import xarray as xr
from fastapi.testclient import TestClient
from shapely.geometry import box

from floodsim.api import routes_results, routes_runs
from floodsim.api.app import app
from floodsim.domain.geometry import AnalysisArea, GeoBounds, LonLat
from floodsim.domain.manifest import Limitations
from floodsim.domain.rainfall import ConstantRainfall
from floodsim.domain.run_config import AccuracyMode, RunConfig
from floodsim.domain.run_state import RunState
from floodsim.orchestration.rainfall_resolution import resolve_rainfall
from floodsim.orchestration.run_coordinator import (
    DEFAULT_OSM_REVIEW_BUDGET_S,
    DEFAULT_PLATEAU_REVIEW_BUDGET_S,
    RunCoordinator,
)
from floodsim.preprocessing.full_grid import (
    BUILDING_PERIMETER_MANNING,
    GENERAL_MANNING,
    ROAD_MANNING,
    build_full_1m_grid,
)
from floodsim.preprocessing.roof_rainfall import (
    RoofRunoffNoRecipient,
    allocate_roof_rainfall,
)
from floodsim.providers.common import ProviderProvenance
from floodsim.providers.gsi_elevation import ElevationProduct
from floodsim.results.normalize import normalize_regular_result
from floodsim.sfincs import output_reader
from floodsim.sfincs.model_builder import (
    ModelBuildResult,
    SfincsModelBuilder,
    derive_output_interval_seconds,
)
from floodsim.sfincs.output_reader import (
    SfincsResultError,
    inspect_regular_result,
    read_regular_result,
)
from floodsim.sfincs.runner import (
    ResolvedEngine,
    SfincsProgress,
    SfincsRunResult,
    parse_sfincs_progress_line,
    sfincs_process_environment,
)


def _area(size: int = 4) -> AnalysisArea:
    return AnalysisArea(
        mode="rectangle",
        bounds=GeoBounds(west_deg=139.0, south_deg=35.0, east_deg=139.001, north_deg=35.001),
        center=LonLat(lon_deg=139.0005, lat_deg=35.0005),
        width_m=float(size),
        height_m=float(size),
        area_m2=float(size * size),
    )


def _provenance(provider_id: str, area: AnalysisArea, **details: object) -> ProviderProvenance:
    return ProviderProvenance.create(
        provider_id,
        provider_id,
        area.bounds,
        provider_id,
        "https://example.invalid/terms",
        source_details=details,
        acquired_at_utc="2026-01-01T00:00:00+00:00",
    )


def _elevation(area: AnalysisArea, grid_m: float = 1.0) -> ElevationProduct:
    size = int(area.width_m / grid_m)
    z = np.zeros((size + 1, size + 1), dtype=np.float32)
    return ElevationProduct(
        z=z,
        x=np.linspace(-size / 2, size / 2, size + 1, dtype=np.float32),
        y=np.linspace(-size / 2, size / 2, size + 1, dtype=np.float32),
        source=np.ones_like(z, dtype=np.uint8),
        source_names=["DEM1A"],
        nearest_filled=0,
        provenance=_provenance("gsi", area, provider_counts={"DEM1A": z.size}),
    )


def _vectors(area: AnalysisArea, *, with_building: bool = True) -> SimpleNamespace:
    building = np.asarray(box(-1.1, -1.1, 1.1, 1.1).exterior.coords, dtype=float)
    road = np.asarray([[-1.5, -1.5], [1.5, -1.5]], dtype=float)
    return SimpleNamespace(
        buildings=[building] if with_building else [],
        road_lines=[road],
        road_polygons=[],
        provenance=_provenance("plateau", area),
    )


def test_half_metre_grid_builds_four_cells_per_square_metre() -> None:
    area = _area()
    elevation = replace(_elevation(area), z=np.zeros((9, 9), dtype=np.float32),
        x=np.linspace(-2, 2, 9), y=np.linspace(-2, 2, 9), source=np.ones((9, 9), dtype=np.uint8))
    updates = []
    grid = build_full_1m_grid(area, elevation, _vectors(area), grid_m=0.5,
        progress_callback=lambda fraction, message: updates.append(message))
    assert grid.elevation_m.shape == (8, 8)
    assert grid.cell_count == 64
    assert grid.dx_m == grid.dy_m == 0.5
    assert np.all(grid.sfincs_mask[grid.building_mask] == 0)
    assert grid.roof_allocation.hydraulic_weighted_area_m2 == pytest.approx(16)
    assert "0.5 m" in updates[-1]


def test_roof_rainfall_conserves_mass_and_blocks_roof() -> None:
    building = np.zeros((7, 7), dtype=bool)
    building[3, 3] = True
    allocation = allocate_roof_rainfall(building)
    assert allocation.rain_weight[3, 3] == 0
    assert np.count_nonzero(allocation.rain_weight > 1) == 8
    assert allocation.relative_mass_error <= 1e-9
    assert allocation.hydraulic_weighted_area_m2 == pytest.approx(49.0)


def test_roof_rainfall_fails_without_recipient() -> None:
    with pytest.raises(RoofRunoffNoRecipient):
        allocate_roof_rainfall(np.ones((3, 3), dtype=bool))


def test_full_grid_skips_malformed_vector_features() -> None:
    area = _area()
    vectors = _vectors(area)
    vectors.buildings.extend(
        [
            np.asarray([[np.nan, 0.0], [1.0, 0.0], [1.0, 1.0]]),
            np.asarray([[0.0], [1.0], [2.0]]),
        ]
    )
    vectors.road_lines.extend(
        [
            np.asarray([[0.0, np.inf], [1.0, 1.0]]),
            np.asarray([[0.0], [1.0]]),
        ]
    )

    grid = build_full_1m_grid(area, _elevation(area), vectors)

    assert grid.cell_count == 16
    assert np.any(grid.building_mask)


def test_full_grid_one_metre_inset_drops_tiny_roof() -> None:
    area = _area()
    tiny = np.asarray(box(0.10, 0.10, 0.20, 0.20).exterior.coords, dtype=float)
    vectors = _vectors(area, with_building=False)
    vectors.buildings = [tiny]

    grid = build_full_1m_grid(area, _elevation(area), vectors)

    assert not np.any(grid.building_mask)
    assert grid.sfincs_mask[2, 2] == 1


def test_full_grid_one_metre_inset_keeps_roof_edge_cells_active() -> None:
    area = _area()
    boundary_only = np.asarray(box(-0.5, -0.5, 0.5, 0.5).exterior.coords, dtype=float)
    vectors = _vectors(area, with_building=False)
    vectors.buildings = [boundary_only]

    grid = build_full_1m_grid(area, _elevation(area), vectors)

    assert not np.any(grid.building_mask)
    assert np.all(grid.sfincs_mask[1:3, 1:3] == 1)


@pytest.mark.parametrize("grid_m", [1.0, 2.0, 4.0])
def test_all_uniform_grids_drop_buildings_removed_by_half_metre_inset(
    grid_m: float,
) -> None:
    area = _area(size=8)
    tiny = np.asarray(box(0.10, 0.10, 0.20, 0.20).exterior.coords, dtype=float)
    vectors = _vectors(area, with_building=False)
    vectors.buildings = [tiny]

    grid = build_full_1m_grid(area, _elevation(area, grid_m), vectors, grid_m=grid_m)

    assert not np.any(grid.building_mask)


def test_full_grid_reports_remaining_preprocessing_and_roof_work() -> None:
    area = _area()
    updates: list[tuple[float, str]] = []

    grid = build_full_1m_grid(
        area,
        _elevation(area),
        _vectors(area),
        progress_callback=lambda fraction, message: updates.append((fraction, message)),
    )

    assert grid.cell_count == 16
    assert any("残り3処理" in message for _, message in updates)
    assert any("残り0処理" in message for _, message in updates)
    assert any(
        "屋根雨水配分" in message
        and "連結建物群" in message
        and "取得ポリゴン1件" in message
        and "残り" in message
        for _, message in updates
    )
    assert updates[-1][0] == pytest.approx(1.0)
    assert updates[-1][1] == "Full 1 m格子・建物マスク・粗度の構築完了"


def test_full_grid_sets_building_boundary_and_manning() -> None:
    area = _area()
    grid = build_full_1m_grid(area, _elevation(area), _vectors(area))
    assert grid.elevation_m.shape == (4, 4)
    assert grid.cell_count == 16
    assert np.any(grid.building_mask)
    assert np.all(grid.sfincs_mask[grid.building_mask] == 0)
    boundary = np.zeros((4, 4), dtype=bool)
    boundary[[0, -1], :] = True
    boundary[:, [0, -1]] = True
    assert np.all(grid.sfincs_mask[boundary & ~grid.building_mask] == 1)
    perimeter = np.zeros_like(grid.building_mask, dtype=bool)
    perimeter[1:, :] |= grid.building_mask[:-1, :]
    perimeter[:-1, :] |= grid.building_mask[1:, :]
    perimeter[:, 1:] |= grid.building_mask[:, :-1]
    perimeter[:, :-1] |= grid.building_mask[:, 1:]
    perimeter[1:, 1:] |= grid.building_mask[:-1, :-1]
    perimeter[1:, :-1] |= grid.building_mask[:-1, 1:]
    perimeter[:-1, 1:] |= grid.building_mask[1:, :-1]
    perimeter[:-1, :-1] |= grid.building_mask[1:, 1:]
    perimeter &= ~grid.building_mask
    assert np.all(np.isclose(grid.manning_n[perimeter], BUILDING_PERIMETER_MANNING))
    assert ROAD_MANNING == pytest.approx(0.030)
    assert GENERAL_MANNING == pytest.approx(0.050)
    assert BUILDING_PERIMETER_MANNING == pytest.approx(0.060)
    assert grid.roof_allocation.relative_mass_error <= 1e-9


def test_full_grid_turns_coastline_into_outflow_boundary() -> None:
    area = _area()
    elevation = _elevation(area)
    elevation.uncovered_boundary_mask = np.zeros((5, 5), dtype=bool)
    elevation.uncovered_boundary_mask[:, :2] = True

    grid = build_full_1m_grid(area, elevation, _vectors(area, with_building=False))

    assert np.all(grid.sfincs_mask[:, 0] == 0)
    assert np.all(grid.sfincs_mask[[0, -1], 1] == 1)
    assert np.all(grid.sfincs_mask[1:-1, 1] == 3)
    assert grid.roof_allocation.meteorological_area_m2 == pytest.approx(12.0)
    assert grid.roof_allocation.hydraulic_weighted_area_m2 == pytest.approx(12.0)


def test_full_grid_excludes_buildings_outside_the_active_land_domain() -> None:
    area = _area()
    elevation = _elevation(area)
    elevation.uncovered_boundary_mask = np.zeros((5, 5), dtype=bool)
    elevation.uncovered_boundary_mask[:, :2] = True
    vectors = _vectors(area, with_building=False)
    vectors.buildings = [
        np.asarray(box(-2.0, -2.0, -1.05, 2.0).exterior.coords, dtype=float),
    ]

    grid = build_full_1m_grid(area, elevation, vectors)

    assert not np.any(grid.building_mask[:, 0])
    assert grid.roof_allocation.redistributed_roof_cells == 0
    assert grid.roof_allocation.meteorological_area_m2 == pytest.approx(12.0)
    assert grid.roof_allocation.hydraulic_weighted_area_m2 == pytest.approx(12.0)


def test_uniform_four_metre_grid_uses_four_metre_cells_end_to_end(tmp_path: Path) -> None:
    area = _area(8)
    grid = build_full_1m_grid(
        area,
        _elevation(area, grid_m=4.0),
        _vectors(area, with_building=False),
        grid_m=4.0,
    )

    assert grid.elevation_m.shape == (2, 2)
    assert grid.dx_m == pytest.approx(4.0)
    assert grid.dy_m == pytest.approx(4.0)
    assert grid.cell_count == 4

    result = SfincsModelBuilder().build(
        tmp_path / "uniform-4m",
        grid,
        resolve_rainfall(
            RunConfig(
                analysis_area=area,
                requested_accuracy_mode=AccuracyMode.UNIFORM,
                grid_cell_size_m=4,
                rainfall=ConstantRainfall(intensity_mm_per_h=60, duration_minutes=1),
            )
        ),
    )

    assert result.report["grid_resolution_m"] == pytest.approx(4.0)
    assert result.report["cell_counts"] == {"4m": 4}


@pytest.mark.parametrize("host_debug", ["release", "1"])
def test_real_sfincs_builder_writes_with_host_debug(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    host_debug: str,
) -> None:
    monkeypatch.setenv("DEBUG", host_debug)
    area = _area()
    grid = build_full_1m_grid(area, _elevation(area), _vectors(area))

    result = SfincsModelBuilder().build(
        tmp_path / "model", grid, resolve_rainfall(_config())
    )

    assert os.environ["DEBUG"] == host_debug
    assert result.report_path.is_file()
    assert result.report["grid_resolution_m"] == 1.0
    assert result.report["roof_rain_relative_mass_error"] <= 1e-9
    config = (result.model_dir / "sfincs.inp").read_text(encoding="utf-8")
    assert "netamprfile          = sfincs_netampr.nc" in config
    assert "storevel             = 1" in config
    assert result.report["velocity_output"]["storevel"] == 1
    assert "epsg" not in config.lower()
    assert "debug" not in config.lower()
    with xr.open_dataset(result.model_dir / "sfincs_netampr.nc") as precipitation:
        values = precipitation["Precipitation"]
        assert values.dims == ("time", "y", "x")
        assert precipitation.sizes["time"] == 2
        np.testing.assert_array_equal(
            precipitation["time"].values,
            np.asarray(
                ["2000-01-01T00:00:00", "2000-01-01T00:01:00"], dtype="datetime64[s]"
            ),
        )
        assert float(values.isel(time=0).sum()) == pytest.approx(60.0 * grid.cell_count)
        assert float(values.isel(time=1).sum()) == pytest.approx(60.0 * grid.cell_count)


def test_full_builder_uses_explicit_performance_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DEBUG", raising=False)
    area = _area()
    grid = build_full_1m_grid(area, _elevation(area), _vectors(area))
    config = _config().model_copy(
        update={
            "rainfall": ConstantRainfall(
                intensity_mm_per_h=60,
                duration_minutes=10,
            )
        }
    )

    result = SfincsModelBuilder().build(
        tmp_path / "optimized-model",
        grid,
        resolve_rainfall(config),
    )

    settings: dict[str, str] = {}
    for line in (result.model_dir / "sfincs.inp").read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip().lower()] = value.strip()

    assert float(settings["dtmapout"]) == pytest.approx(60.0)
    assert float(settings["dtmaxout"]) == pytest.approx(600.0)
    assert int(float(settings["storecumprcp"])) == 0
    assert int(float(settings["storevel"])) == 1
    assert float(settings["alpha"]) == pytest.approx(0.70)
    assert float(settings["huthresh"]) == pytest.approx(0.005)
    assert "bndfile" not in settings
    assert "bzsfile" not in settings
    assert not (result.model_dir / "sfincs.bnd").exists()
    assert not (result.model_dir / "sfincs.bzs").exists()
    assert result.report["neumann_boundary_cells"] == 0
    assert result.report["neumann_boundary_compatibility_boundary_file"] is False

    assert result.report["output_interval_seconds"] == 60
    assert result.report["maximum_output_interval_seconds"] == pytest.approx(600.0)
    assert result.report["cumulative_precipitation_output"] is False
    assert result.report["velocity_output"]["storevel"] == 1
    assert result.report["numerics"]["alpha"] == pytest.approx(0.70)
    assert result.report["numerics"]["huthresh_m"] == pytest.approx(0.005)


def test_full_builder_can_reduce_saved_frame_frequency_without_changing_solver(
    tmp_path: Path,
) -> None:
    area = _area()
    grid = build_full_1m_grid(area, _elevation(area), _vectors(area))
    config = _config().model_copy(
        update={
            "rainfall": ConstantRainfall(
                intensity_mm_per_h=60,
                duration_minutes=60,
            )
        }
    )

    result = SfincsModelBuilder(minimum_output_interval_seconds=900).build(
        tmp_path / "sparse-output-model",
        grid,
        resolve_rainfall(config),
    )

    settings = {
        key.strip().lower(): value.strip()
        for line in (result.model_dir / "sfincs.inp").read_text(encoding="utf-8").splitlines()
        if "=" in line
        for key, value in [line.split("=", 1)]
    }
    assert float(settings["dtmapout"]) == pytest.approx(900.0)
    assert float(settings["dthisout"]) == pytest.approx(900.0)
    assert float(settings["dtmaxout"]) == pytest.approx(3600.0)
    assert result.report["output_interval_seconds"] == 900


def _write_synthetic_result(
    path: Path,
    *,
    nonfinite: bool = False,
    missing_hmax: bool = False,
    with_velocity: bool = False,
    one_sided_velocity: bool = False,
    hmax_frames: int = 1,
) -> None:
    h = np.asarray(
        [[[0.0, 0.01], [0.02, 0.03]], [[0.0, 0.02], [0.04, 0.05]]],
        dtype=np.float32,
    )
    if nonfinite:
        h[0, 0, 0] = np.nan
    hmax = np.nanmax(h, axis=0, keepdims=True)
    if hmax_frames < 1:
        raise ValueError("hmax_frames must be positive")
    if hmax_frames > 1:
        hmax = np.concatenate(
            [hmax * (index + 1) / hmax_frames for index in range(hmax_frames)],
            axis=0,
        )
    if missing_hmax:
        hmax[:, 1, 1] = np.nan
    data_vars = {
        "h": (("time", "n", "m"), h),
        "hmax": (("timemax", "n", "m"), hmax),
        "zs": (("time", "n", "m"), h + 1.0),
        "zb": (("n", "m"), np.ones((2, 2), dtype=np.float32)),
        "msk": (("n", "m"), np.ones((2, 2), dtype=np.int16)),
    }
    if with_velocity or one_sided_velocity:
        data_vars["u"] = (
            ("time", "n", "m"),
            np.asarray(
                [[[0.0, 0.1], [0.2, 0.3]], [[0.0, 0.2], [0.3, 0.4]]],
                dtype=np.float32,
            ),
        )
    if with_velocity:
        data_vars["v"] = (
            ("time", "n", "m"),
            np.asarray(
                [[[0.0, 0.0], [0.1, 0.1]], [[0.0, 0.1], [0.2, 0.2]]],
                dtype=np.float32,
            ),
        )
    dataset = xr.Dataset(
        data_vars,
        coords={"time": [0, 60], "timemax": [60 * (index + 1) for index in range(hmax_frames)]},
    )
    dataset.to_netcdf(path)


def test_output_reader_and_normalizer_expose_max_depth(tmp_path: Path) -> None:
    result_path = tmp_path / "sfincs_map.nc"
    _write_synthetic_result(result_path)
    result = read_regular_result(result_path)
    assert result.global_max_depth_m == pytest.approx(0.05)
    normalized = normalize_regular_result(
        result,
        area=_area(2),
        results_dir=tmp_path / "normalized",
        limitations=Limitations(),
    )
    assert normalized.metadata["max_depth_summary"]["global_max_depth_m"] == pytest.approx(0.05)
    assert normalized.arrays_path.is_file()


def test_regular_result_inspection_keeps_native_time_axis_without_decoding_grid(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "sfincs_map.nc"
    _write_synthetic_result(result_path)

    descriptor = inspect_regular_result(result_path)

    assert descriptor.path == result_path
    assert descriptor.time_values == ("0", "60")
    assert (descriptor.height, descriptor.width) == (2, 2)


def test_output_reader_accepts_single_and_multiple_timemax_frames(
    tmp_path: Path,
) -> None:
    single_path = tmp_path / "single_hmax.nc"
    _write_synthetic_result(single_path, hmax_frames=1)
    single = read_regular_result(single_path)

    legacy_path = tmp_path / "legacy_multi_hmax.nc"
    _write_synthetic_result(legacy_path, hmax_frames=3)
    legacy = read_regular_result(legacy_path)

    np.testing.assert_allclose(
        single.max_depth_m,
        legacy.max_depth_m,
        rtol=0.0,
        atol=1e-7,
        equal_nan=True,
    )
    assert single.global_max_depth_m == pytest.approx(legacy.global_max_depth_m)


def test_output_reader_persists_paired_velocity_and_keeps_old_results_compatible(
    tmp_path: Path,
) -> None:
    old_path = tmp_path / "old_no_velocity.nc"
    _write_synthetic_result(old_path)
    old_result = read_regular_result(old_path)
    assert old_result.flow_vectors_available is False

    path = tmp_path / "with_velocity.nc"
    _write_synthetic_result(path, with_velocity=True)
    result = read_regular_result(path)
    assert result.flow_vectors_available is True
    assert result.velocity_u_mps is not None
    assert result.velocity_v_mps is not None
    assert result.velocity_u_mps.shape == result.depth_time_m.shape

    normalized = normalize_regular_result(
        result,
        area=_area(2),
        results_dir=tmp_path / "normalized_velocity",
        limitations=Limitations(),
    )
    assert normalized.metadata["flow_vectors_available"] is True
    with np.load(normalized.arrays_path, allow_pickle=False) as archive:
        assert "velocity_u_mps" in archive.files
        assert "velocity_v_mps" in archive.files


def test_output_reader_downsamples_large_velocity_grids_for_display(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "downsampled_velocity.nc"
    values = np.ones((2, 5, 5), dtype=np.float32)
    xr.Dataset(
        {
            "h": (("time", "n", "m"), values),
            "hmax": (("timemax", "n", "m"), values[:1]),
            "zs": (("time", "n", "m"), values),
            "zb": (("n", "m"), np.zeros((5, 5), dtype=np.float32)),
            "msk": (("n", "m"), np.ones((5, 5), dtype=np.int16)),
            "u": (("time", "n", "m"), values),
            "v": (("time", "n", "m"), values),
        },
        coords={"time": [0, 60], "timemax": [60]},
    ).to_netcdf(path)
    monkeypatch.setattr(output_reader, "MAX_FLOW_VECTOR_CELLS_PER_FRAME", 4)

    result = read_regular_result(path)

    assert result.velocity_grid_stride == 3
    assert result.velocity_u_mps is not None
    assert result.velocity_u_mps.shape == (2, 2, 2)
    normalized = normalize_regular_result(
        result,
        area=_area(5),
        results_dir=tmp_path / "normalized_downsampled_velocity",
        limitations=Limitations(),
    )
    with np.load(normalized.arrays_path, allow_pickle=False) as archive:
        assert int(archive["velocity_grid_stride"].item()) == 3


def test_output_reader_rejects_one_sided_velocity_output(tmp_path: Path) -> None:
    path = tmp_path / "one_sided_velocity.nc"
    _write_synthetic_result(path, one_sided_velocity=True)
    with pytest.raises(SfincsResultError, match="both u and v"):
        read_regular_result(path)


def test_output_reader_reconstructs_missing_active_hmax_from_depth(tmp_path: Path) -> None:
    path = tmp_path / "sparse_hmax.nc"
    _write_synthetic_result(path, missing_hmax=True)

    result = read_regular_result(path)

    assert result.hmax_reconstructed_cells == 1
    assert result.max_depth_m[1, 1] == pytest.approx(0.05)
    normalized = normalize_regular_result(
        result,
        area=_area(2),
        results_dir=tmp_path / "normalized_sparse_hmax",
        limitations=Limitations(),
    )
    assert normalized.metadata["max_depth_summary"]["hmax_reconstructed_cells"] == 1
    assert normalized.metadata["max_depth_summary"]["global_max_depth_m"] == pytest.approx(0.05)


def test_output_reader_normalizes_negative_regular_depth_but_keeps_hmax_strict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dry_negative_depth.nc"
    _write_synthetic_result(path)
    with xr.open_dataset(path) as dataset:
        rewritten = dataset.load()

    # SFINCS regular-grid h is written as unfiltered zs-zb. Reproduce the
    # reported dry-cell excursion while keeping hmax absent for that never-wet cell.
    rewritten["h"].values[0, 0, 0] = -0.050426
    rewritten["hmax"].values[:, 0, 0] = np.nan
    rewritten.to_netcdf(path, mode="w")

    result = read_regular_result(path)
    assert result.depth_time_m[0, 0, 0] == 0.0
    assert result.max_depth_m[0, 0] == 0.0
    assert result.negative_depth_clipped_values == 1
    assert result.negative_max_depth_clipped_cells == 0
    assert result.min_raw_active_depth_m == pytest.approx(-0.050426)
    assert result.hmax_reconstructed_cells == 1

    normalized = normalize_regular_result(
        result,
        area=_area(2),
        results_dir=tmp_path / "normalized_dry_negative",
        limitations=Limitations(),
    )
    summary = normalized.metadata["max_depth_summary"]
    assert summary["negative_depth_clipped_values"] == 1
    assert summary["min_raw_active_depth_m"] == pytest.approx(-0.050426)
    assert "unfiltered signed zs-zb" in normalized.metadata["no_data_policy"]

    # hmax is wet-filtered by SFINCS, so a finite negative maximum is still
    # inconsistent and must remain a hard quality failure.
    rewritten["hmax"].values[:, 0, 0] = -0.001
    rewritten.to_netcdf(path, mode="w")
    with pytest.raises(SfincsResultError, match="maximum depth contains negative"):
        read_regular_result(path)


def test_output_reader_excludes_sfincs_boundary_control_cells(tmp_path: Path) -> None:
    path = tmp_path / "boundary_depth.nc"
    _write_synthetic_result(path)
    with xr.open_dataset(path) as dataset:
        rewritten = dataset.load()

    rewritten["msk"].values[0, 0] = 6
    rewritten["h"].values[:, 0, 0] = -5.0
    rewritten["hmax"].values[:, 0, 0] = -5.0
    rewritten.to_netcdf(path, mode="w")

    result = read_regular_result(path)

    assert not result.active_mask[0, 0]
    assert np.isnan(result.depth_time_m[:, 0, 0]).all()
    assert np.isnan(result.max_depth_m[0, 0])
    assert np.isnan(result.terrain_elevation_m[0, 0])
    assert result.excluded_boundary_cells == 1
    assert result.negative_depth_clipped_values == 0


def test_output_reader_rejects_nonfinite_active_depth(tmp_path: Path) -> None:
    path = tmp_path / "bad.nc"
    _write_synthetic_result(path, nonfinite=True)
    with pytest.raises(SfincsResultError):
        read_regular_result(path)


def test_output_interval_is_whole_minute_and_bounded() -> None:
    assert derive_output_interval_seconds(60) == 60
    assert derive_output_interval_seconds(3600) == 60
    assert derive_output_interval_seconds(12 * 3600) % 60 == 0
    assert 60 <= derive_output_interval_seconds(12 * 3600) <= 900


def test_sfincs_progress_parser_uses_official_percent_line() -> None:
    parsed = parse_sfincs_progress_line("  40% complete,    18.5 s remaining ...")
    assert parsed is not None
    assert parsed.fraction == pytest.approx(0.40)
    assert parsed.engine_reported_remaining_seconds == pytest.approx(18.5)
    assert parse_sfincs_progress_line("Starting computation ...") is None


def test_sfincs_process_environment_requests_all_logical_cpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    env = sfincs_process_environment({"PATH": "test"})
    assert env["PATH"] == "test"
    assert env["OMP_NUM_THREADS"] == "12"
    assert env["OMP_DYNAMIC"] == "FALSE"




class _FakeElevationProvider:
    def acquire(self, area: AnalysisArea, **kwargs: object) -> ElevationProduct:
        return _elevation(area, float(kwargs.get("grid_m", 1.0)))


class _BlockingElevationProvider:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def acquire(self, area: AnalysisArea, **_: object) -> ElevationProduct:
        self.entered.set()
        assert self.release.wait(timeout=10)
        return _elevation(area)


class _FakeModelBuilder:
    def build(self, model_dir: Path, grid: object, rainfall: object) -> ModelBuildResult:
        model_dir.mkdir(parents=True, exist_ok=True)
        report = model_dir / "model_build_report.json"
        report.write_text("{}\n", encoding="utf-8")
        return ModelBuildResult(model_dir=model_dir, report_path=report, report={})


class _FakeRunner:
    def cancel(self) -> None:
        return None

    def run(
        self,
        model_dir: Path,
        *,
        logs_dir: Path,
        engine: ResolvedEngine,
        cancel_event: object,
        progress_callback=None,
        line_callback=None,
    ) -> SfincsRunResult:
        logs_dir.mkdir(parents=True, exist_ok=True)
        result = model_dir / "sfincs_map.nc"
        _write_synthetic_result(result)
        stdout = logs_dir / "sfincs.stdout.log"
        stderr = logs_dir / "sfincs.stderr.log"
        stdout.write_text("ok\n", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        if line_callback is not None:
            line_callback("---- Using 8 of 8 available threads ----")
            line_callback("50% complete, 1.0 s remaining")
        if progress_callback is not None:
            progress_callback(SfincsProgress(0.5, 1.0))
        return SfincsRunResult(0, result, stdout, stderr, engine, elapsed_seconds=2.0)


def _test_coordinator(tmp_path: Path) -> RunCoordinator:
    return RunCoordinator(
        runs_root=tmp_path / "runs",
        elevation_provider=_FakeElevationProvider(),
        vector_acquirer=lambda area, **_kwargs: _vectors(area, with_building=False),
        model_builder=_FakeModelBuilder(),
        engine_resolver=lambda: ResolvedEngine(Path(__file__), "test", "TESTSHA"),
        runner_factory=_FakeRunner,
    )


def _config() -> RunConfig:
    return RunConfig(
        analysis_area=_area(2),
        requested_accuracy_mode=AccuracyMode.FULL_1M,
        rainfall=ConstantRainfall(intensity_mm_per_h=60, duration_minutes=1),
    )


def test_coordinator_runs_full_1m_to_normalized_result(tmp_path: Path) -> None:
    coordinator = _test_coordinator(tmp_path)
    record = coordinator.create_run(_config())
    assert record.future is not None
    record.future.result(timeout=10)
    assert record.machine.state is RunState.COMPLETE
    metadata = coordinator.result_metadata(record.run_id)
    assert metadata["max_depth_summary"]["global_max_depth_m"] == pytest.approx(0.05)
    manifest = coordinator.store.read_manifest(record.run_id)
    assert manifest is not None
    assert manifest["run_status"] == "COMPLETE"
    assert manifest["limitations"]["infiltration_modelled"] is False
    assert manifest["limitations"]["sewer_network_modelled"] is False
    assert any("Using 8 of 8 available threads" in line for line in record.activity_lines)
    assert any("50% complete" in line for line in record.activity_lines)
    assert manifest["roof_rain_mass_diagnostic"]["relative_error"] <= 1e-9
    assert [event.sequence for event in record.events] == list(range(1, len(record.events) + 1))


def test_coordinator_reuses_prepared_grid_for_same_area_changed_rainfall(
    tmp_path: Path,
) -> None:
    calls = {"elevation": 0, "vectors": 0, "grid": 0}

    class CountingElevation:
        def acquire(self, area: AnalysisArea, **_: object) -> ElevationProduct:
            calls["elevation"] += 1
            return _elevation(area)

    def vectors(area: AnalysisArea, **_: object):
        calls["vectors"] += 1
        return _vectors(area, with_building=False)

    def grid(area: AnalysisArea, elevation: ElevationProduct, vector_data: object):
        calls["grid"] += 1
        return build_full_1m_grid(area, elevation, vector_data)

    coordinator = RunCoordinator(
        runs_root=tmp_path / "runs",
        elevation_provider=CountingElevation(),
        vector_acquirer=vectors,
        grid_builder=grid,
        model_builder=_FakeModelBuilder(),
        engine_resolver=lambda: ResolvedEngine(Path(__file__), "test", "TESTSHA"),
        runner_factory=_FakeRunner,
    )

    first = coordinator.create_run(_config())
    assert first.future is not None
    first.future.result(timeout=10)
    assert first.machine.state is RunState.COMPLETE
    assert calls == {"elevation": 1, "vectors": 1, "grid": 1}

    changed_rain = _config().model_copy(
        update={"rainfall": ConstantRainfall(intensity_mm_per_h=120, duration_minutes=1)}
    )
    second = coordinator.create_run(changed_rain)
    assert second.future is not None
    second.future.result(timeout=10)
    assert second.machine.state is RunState.COMPLETE
    assert calls == {"elevation": 1, "vectors": 1, "grid": 1}

    manifest = coordinator.store.read_manifest(second.run_id)
    assert manifest is not None
    assert manifest["elevation_source_summary"]["prepared_cache_hit"] is True
    assert manifest["rainfall_source"] != coordinator.store.read_manifest(first.run_id)["rainfall_source"]


def test_coordinator_cancels_while_provider_is_blocked(tmp_path: Path) -> None:
    elevation_provider = _BlockingElevationProvider()
    coordinator = _test_coordinator(tmp_path)
    coordinator.elevation_provider = elevation_provider
    record = coordinator.create_run(_config())
    assert record.future is not None
    assert elevation_provider.entered.wait(timeout=5)

    coordinator.cancel(record.run_id)

    assert record.machine.state is RunState.CANCELLED
    manifest = coordinator.store.read_manifest(record.run_id)
    assert manifest is not None
    assert manifest["run_status"] == "CANCELLED"

    elevation_provider.release.set()
    record.future.result(timeout=5)
    assert record.machine.state is RunState.CANCELLED


def test_coordinator_persists_unexpected_grid_failure_diagnostic(tmp_path: Path) -> None:
    coordinator = _test_coordinator(tmp_path)

    def fail_grid(*_args: object, **_kwargs: object) -> object:
        raise ValueError("synthetic grid failure")

    coordinator.grid_builder = fail_grid
    record = coordinator.create_run(_config())
    assert record.future is not None
    record.future.result(timeout=10)

    assert record.machine.state is RunState.FAILED
    manifest = coordinator.store.read_manifest(record.run_id)
    assert manifest is not None
    assert manifest["failure_code"] == "INTERNAL_RUN_FAILED"
    assert manifest["failing_stage"] == "BUILDING_GRID"
    assert manifest["failure_exception_type"] == "ValueError"
    assert manifest["failure_message"] == "synthetic grid failure"
    assert manifest["failure_diagnostic_file"] == "logs/failure_diagnostic.json"

    diagnostic_path = coordinator.store.run_dir(record.run_id) / manifest["failure_diagnostic_file"]
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["stage"] == "BUILDING_GRID"
    assert diagnostic["exception_type"] == "ValueError"
    assert diagnostic["message"] == "synthetic grid failure"
    assert "synthetic grid failure" in diagnostic["traceback"]
    assert diagnostic["runtime"]["grid_input"]["vectors"]["provider_id"] == "plateau"
    assert diagnostic["runtime"]["grid_input"]["elevation"]["shape"] == [3, 3]


def test_phase3_api_accepts_run_and_exposes_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    coordinator = _test_coordinator(tmp_path)
    monkeypatch.setattr(routes_runs, "coordinator", coordinator)
    monkeypatch.setattr(routes_results, "coordinator", coordinator)
    client = TestClient(app)
    response = client.post("/api/v1/runs", json=_config().model_dump(mode="json"))
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    record = coordinator.get(UUID(run_id))
    assert record.future is not None
    record.future.result(timeout=10)
    status = client.get(f"/api/v1/runs/{run_id}")
    assert status.status_code == 200
    assert status.json()["state"] == "COMPLETE"
    assert any("Using 8 of 8 available threads" in line for line in status.json()["activity_lines"])
    metadata = client.get(f"/api/v1/runs/{run_id}/result-metadata")
    assert metadata.status_code == 200
    assert metadata.json()["max_depth_summary"]["global_max_depth_m"] == pytest.approx(0.05)


def test_run_continues_without_browser_heartbeat_lease(tmp_path: Path) -> None:
    coordinator = _test_coordinator(tmp_path)
    record = coordinator.create_run(_config())
    assert record.future is not None

    # No status request is made here. Browser observation is intentionally
    # decoupled from the worker, so only the explicit cancel endpoint can
    # request cancellation.
    record.future.result(timeout=10)

    assert record.machine.state is RunState.COMPLETE
    assert not hasattr(coordinator, "enable_client_lease")
    assert not hasattr(coordinator, "client_heartbeat")


def test_api_marks_persisted_inflight_run_as_interrupted_after_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original = _test_coordinator(tmp_path)
    elevation_provider = _BlockingElevationProvider()
    original.elevation_provider = elevation_provider
    record = original.create_run(_config())
    assert record.future is not None
    assert elevation_provider.entered.wait(timeout=5)

    restarted = _test_coordinator(tmp_path)
    monkeypatch.setattr(routes_runs, "coordinator", restarted)
    response = TestClient(app).get(f"/api/v1/runs/{record.run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "FAILED"
    assert payload["failure_code"] == "RUN_INTERRUPTED"
    assert "再起動" in payload["failure_message"]
    manifest = restarted.store.read_manifest(record.run_id)
    assert manifest is not None
    assert manifest["run_status"] == "FAILED"
    original.cancel(record.run_id)
    elevation_provider.release.set()
    record.future.result(timeout=10)


def test_run_mutation_requires_json_content_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    coordinator = _test_coordinator(tmp_path)
    monkeypatch.setattr(routes_runs, "coordinator", coordinator)
    client = TestClient(app)
    response = client.post("/api/v1/runs", content="{}", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "INPUT_UNSUPPORTED_CONTENT_TYPE"



def test_coordinator_passes_bounded_vector_provider_budgets(tmp_path: Path) -> None:
    observed: dict[str, object] = {}

    def vector_acquirer(area: AnalysisArea, **kwargs: object):
        observed.update(kwargs)
        return _vectors(area, with_building=False)

    coordinator = RunCoordinator(
        runs_root=tmp_path / "runs",
        elevation_provider=_FakeElevationProvider(),
        vector_acquirer=vector_acquirer,
        model_builder=_FakeModelBuilder(),
        engine_resolver=lambda: ResolvedEngine(Path(__file__), "test", "TESTSHA"),
        runner_factory=_FakeRunner,
    )
    record = coordinator.create_run(_config())
    assert record.future is not None
    record.future.result(timeout=10)

    assert observed["plateau_budget_s"] == DEFAULT_PLATEAU_REVIEW_BUDGET_S
    assert observed["osm_budget_s"] == DEFAULT_OSM_REVIEW_BUDGET_S
    assert observed["cancel_event"] is record.cancel_event


def test_flow_arrow_length_scales_with_sampling_spacing() -> None:
    from floodsim.results.view import _display_arrow_length_m

    for stride, expected_length in ((1, 0.8), (2, 1.6), (4, 3.2), (8, 6.4)):
        assert _display_arrow_length_m(sample_span_m=float(stride)) == pytest.approx(
            expected_length
        )
        assert (
            _display_arrow_length_m(sample_span_m=float(stride)) / float(stride)
            == pytest.approx(0.8)
        )


