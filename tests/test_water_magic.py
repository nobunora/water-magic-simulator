import json
import struct
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from floodsim.domain.geometry import AnalysisArea, GeoBounds, LonLat
from floodsim.domain.rainfall import ConstantRainfall
from floodsim.domain.run_config import AccuracyMode, RunConfig
from floodsim.domain.water_magic import WaterMagicConfig, magic_output_interval
from floodsim.orchestration.rainfall_resolution import resolve_rainfall
from floodsim.preprocessing.full_grid import FullGridProduct
from floodsim.preprocessing.roof_rainfall import allocate_roof_rainfall
from floodsim.providers.common import local_crs
from floodsim.sfincs.model_builder import SfincsModelBuilder
from floodsim.sfincs.water_magic_forcing import (
    MagicForcingError,
    magic_cell_rates,
    write_magic_discharge,
)
from floodsim.sfincs.water_magic_initial import (
    initial_velocity,
    regular_faces,
    write_magic_initial_state,
)


def configuration(**overrides):
    area = AnalysisArea(mode="rectangle", bounds=GeoBounds(west_deg=138.999, south_deg=34.999,
        east_deg=139.001, north_deg=35.001), center=LonLat(lon_deg=139, lat_deg=35),
        width_m=20, height_m=20, area_m2=400)
    magic = WaterMagicConfig(position=area.center, radius_m=3, bearing_deg=0,
        volume_m3=0.1, casting_seconds=5, relaxation_seconds=60, **overrides)
    return RunConfig(analysis_area=area, requested_accuracy_mode=AccuracyMode.FULL_1M, water_magic=magic)


def grid(config):
    shape = (20, 20)
    buildings = np.zeros(shape, dtype=bool)
    return FullGridProduct(np.zeros(shape), buildings, np.ones(shape, dtype=np.uint8),
        np.full(shape, .03), np.ones(shape), allocate_roof_rainfall(buildings),
        20, 20, 1, 1, -10, -10, local_crs(config.analysis_area).to_wkt())


def test_initial_motion_defaults_preserve_old_archives_and_reject_invalid_combinations():
    magic = configuration().water_magic
    assert magic.release_mode == "continuous" and magic.initial_speed_mps == 0
    for updates in [{"initial_speed_mps": 1}, {"initial_motion": "vortex"},
                    {"release_mode": "initial", "initial_motion": "vortex", "initial_speed_mps": 4.1},
                    {"release_mode": "initial", "vortex_core_radius_m": 0}]:
        with pytest.raises(ValidationError):
            WaterMagicConfig.model_validate({**magic.model_dump(), **updates})


def test_generation_rate_times_duration_sets_total_initial_water():
    magic = configuration(release_mode="initial", generation_rate_m3ps=12).water_magic
    assert magic.volume_m3 == 60
    assert WaterMagicConfig.model_validate(magic.model_dump()).volume_m3 == 60
    assert configuration().water_magic.volume_m3 == .1
    with pytest.raises(ValidationError, match="100万"):
        configuration(generation_rate_m3ps=300_000)


def test_vortex_is_tangential_finite_and_reverses_at_center_and_core():
    magic = configuration(release_mode="initial", initial_motion="vortex", initial_speed_mps=2,
                          vortex_core_radius_m=2).water_magic
    x, y = np.array([0., 0., 0., 0., 2.]), np.array([0., 1., 2., 4., 0.])
    u, v = initial_velocity(magic, x, y)
    np.testing.assert_allclose(u, [0, 1, 2, 1, 0])
    np.testing.assert_allclose(v, [0, 0, 0, 0, -2])
    np.testing.assert_allclose(x*u+y*v, 0)
    other = magic.model_copy(update={"vortex_direction":"counterclockwise"})
    reverse_u, reverse_v = initial_velocity(other, x, y)
    np.testing.assert_allclose(reverse_u, -u)
    np.testing.assert_allclose(reverse_v, -v)


def test_face_order_interleaves_east_north_and_excludes_obstacles():
    first, second, direction = regular_faces(np.array([[1, 1, 0], [1, 1, 3]], dtype=np.uint8))
    assert list(zip(first.tolist(), second.tolist(), direction.tolist())) == [
        (0, 2, 0), (0, 1, 1), (1, 3, 0), (2, 3, 1), (3, 5, 0)]


@pytest.mark.parametrize("rate", [None, 12])
def test_restart_records_preserve_volume_and_have_dummy_and_boundary_slots(tmp_path, rate):
    config = configuration(release_mode="initial", initial_motion="directional", initial_speed_mps=2,
                           generation_rate_m3ps=rate)
    product = grid(config)
    product.sfincs_mask[10, 10] = 0
    product.sfincs_mask[10, 11] = 3
    product.building_mask[10, 10] = True
    active = np.flatnonzero(product.sfincs_mask.ravel(order="F") > 0)
    np.array([len(active), *(active+1)], dtype="<u4").tofile(tmp_path/"sfincs.ind")
    product.sfincs_mask.ravel(order="F")[active].tofile(tmp_path/"sfincs.msk")
    np.full(len(active), -1, dtype="<f4").tofile(tmp_path/"sfincs.dep")
    report = write_magic_initial_state(tmp_path, resolve_rainfall(config), product)
    records = []
    with (tmp_path/"water_magic_initial.rst").open("rb") as stream:
        for _ in range(4):
            size, = struct.unpack("<i", stream.read(4))
            records.append(stream.read(size))
            assert struct.unpack("<i", stream.read(4))[0] == size
        assert stream.read() == b""
    assert struct.unpack("<i", records[0])[0] == 1
    levels = np.frombuffer(records[1], dtype="<f4")
    assert np.maximum(levels+1, 0).sum() == pytest.approx(config.water_magic.volume_m3, rel=1e-4)
    first, second, direction = regular_faces(product.sfincs_mask)
    q = np.frombuffer(records[2], dtype="<f4")
    assert len(q) == len(first)+1 and q[-1] == 0
    mask = product.sfincs_mask.ravel(order="F")
    boundary = (mask[first] == 3) | (mask[second] == 3)
    assert len(records[3]) == 4*np.count_nonzero(boundary)
    assert np.all(q[:-1][boundary] == 0)
    assert np.all(q[:-1][direction == 0] == 0)
    assert np.max(q) > 0
    assert report["source_point_count"] == 0
    assert report["relative_volume_error"] < 1e-4


def test_initial_builder_writes_restart_without_continuous_water(tmp_path):
    config = configuration(release_mode="initial", initial_motion="vortex", initial_speed_mps=2)
    source = resolve_rainfall(config)
    build = SfincsModelBuilder().build(tmp_path, grid(config), source)
    text = (tmp_path/"sfincs.inp").read_text()
    assert "water_magic_initial.rst" in text
    assert "srcfile" not in text and "disfile" not in text and "precipfile" not in text
    assert not (tmp_path/"water_magic.dis").exists()
    assert build.report["numerics"]["initial_state"] == "wet_restart"
    assert json.loads(source.source_metadata["source_report_json"])["initial_motion"] == "vortex"


def test_magic_requires_exclusive_source_and_verified_grid():
    config = configuration()
    with pytest.raises(ValidationError, match="exactly one"):
        RunConfig(**{**config.model_dump(), "rainfall": ConstantRainfall(intensity_mm_per_h=20)})
    with pytest.raises(ValidationError, match="Full 1 m"):
        RunConfig(**{**config.model_dump(), "requested_accuracy_mode": "adaptive"})
    with pytest.raises(ValidationError):
        WaterMagicConfig(**{**config.water_magic.model_dump(), "casting_seconds": 1.5})


def test_magic_half_metre_solver_grid_and_resource_limit():
    assert configuration().grid_cell_size_m == 0.5
    payload = configuration().model_dump()
    payload["grid_cell_size_m"] = 0.5
    assert RunConfig.model_validate(payload).grid_cell_size_m == 0.5
    payload["grid_cell_size_m"] = 1
    assert RunConfig.model_validate(payload).grid_cell_size_m == 1
    payload["grid_cell_size_m"] = 0.5
    payload["analysis_area"].update(width_m=1200, height_m=1200, area_m2=1440000)
    with pytest.raises(ValidationError, match="100万格子"):
        RunConfig.model_validate(payload)
    payload = configuration().model_dump()
    payload["grid_cell_size_m"] = 0.25
    with pytest.raises(ValidationError, match="grid size"):
        RunConfig.model_validate(payload)


def test_magic_accepts_the_one_hundred_metre_preset_at_the_api_config_boundary():
    payload = configuration().model_dump()
    payload["analysis_area"].update(mode="preset_square", width_m=200, height_m=200, area_m2=40000)
    accepted = RunConfig.model_validate(payload)
    assert accepted.analysis_area.area_m2 == 40000
    payload["analysis_area"].update(width_m=198, height_m=198, area_m2=39204)
    with pytest.raises(ValidationError, match="half size"):
        RunConfig.model_validate(payload)


def test_time_policy_keeps_cast_and_end_observations_without_minute_rounding():
    assert magic_output_interval(5, 60) == 0.2
    assert magic_output_interval(60, 60) == 0.5
    assert magic_output_interval(6, 60) == 0.2
    assert magic_output_interval(6, 294) == 1
    assert magic_output_interval(60, 300) == 1
    assert magic_output_interval(600, 60) == 10
    with pytest.raises(ValueError, match="600"):
        magic_output_interval(1, 3600)


@pytest.mark.parametrize("kind", ["circle", "domain"])
def test_cell_overlap_conserves_volume_excludes_buildings_and_zeros_tail(tmp_path: Path, kind):
    config = configuration(footprint_kind=kind)
    product = grid(config)
    buildings = product.building_mask.copy()
    buildings[10, 10] = True
    product = replace(product, building_mask=buildings)
    source = resolve_rainfall(config)
    rates, report = magic_cell_rates(source, product)
    assert rates[10, 10] == 0
    assert report["relative_volume_error"] < 1e-6
    if kind == "circle":
        assert report["geometric_area_m2"] == pytest.approx(np.pi * 9, rel=1e-6)
        assert rates[0, 0] == 0
    write_magic_discharge(tmp_path, source, product)
    flows = np.loadtxt(tmp_path / "water_magic.dis")
    volume = np.trapezoid(flows[:, 1:].sum(axis=1), flows[:, 0])
    assert volume == pytest.approx(.1, rel=1e-6)
    assert flows[-1, 0] == 65
    assert np.all(flows[flows[:, 0] >= 5, 1:] == 0)


def test_empty_and_outside_footprints_are_rejected():
    config = configuration()
    product = grid(config)
    with pytest.raises(MagicForcingError, match="地表"):
        magic_cell_rates(resolve_rainfall(config), replace(product, building_mask=np.ones((20, 20), dtype=bool)))
    source = resolve_rainfall(config)
    source.config = source.config.model_copy(update={"radius_m": 20})
    with pytest.raises(MagicForcingError, match="外"):
        magic_cell_rates(source, product)


CATALOG = json.loads((Path(__file__).parents[1] / "web/src/dev/magicCatalog.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("spell", CATALOG, ids=lambda spell: spell["id"])
def test_all_catalog_entries_resolve_conservative_forcing(spell, tmp_path):
    config = configuration()
    product = grid(config)
    # Cover the largest directional footprint without changing the one-metre cells.
    shape = (200, 200)
    buildings = np.zeros(shape, dtype=bool)
    product = replace(product, elevation_m=np.zeros(shape), building_mask=buildings,
        sfincs_mask=np.ones(shape,dtype=np.uint8), width_cells=200, height_cells=200,
        x0_m=-100, y0_m=-100)
    config.water_magic = WaterMagicConfig(spell_id=spell["id"], position=config.analysis_area.center,
        footprint_kind=spell["kind"], radius_m=spell["radius"], length_m=spell["length"], width_m=spell["width"],
        sector_angle_deg=spell["angle"], bearing_deg=90, volume_m3=spell["maxVolume"],
        casting_seconds=spell["casting"], relaxation_seconds=60)
    source = resolve_rainfall(config)
    report = write_magic_discharge(tmp_path, source, product)
    assert report["relative_volume_error"] < 1e-6
    assert report["source_point_count"] > 0
    assert report["configuration"]["spell_id"] == spell["id"]
    data = np.loadtxt(tmp_path/"water_magic.dis")
    assert np.trapezoid(data[:,1:].sum(axis=1),data[:,0]) == pytest.approx(spell["maxVolume"],rel=1e-6)


@pytest.mark.parametrize("kind", ["rectangle", "sector"])
def test_direction_rotates_actual_eligible_cells(kind):
    config = configuration(footprint_kind=kind, length_m=7, width_m=2, sector_angle_deg=90)
    product = grid(config)
    source = resolve_rainfall(config)
    north, report = magic_cell_rates(source,product)
    source.config = source.config.model_copy(update={"bearing_deg":90})
    east, rotated = magic_cell_rates(source,product)
    assert not np.array_equal(north,east)
    # Grid rows increase northward, unlike screen coordinates.
    assert east == pytest.approx(np.rot90(north,1),abs=1e-5)
    assert rotated["geometric_area_m2"] == pytest.approx(report["geometric_area_m2"],rel=1e-8)


@pytest.mark.parametrize("spell_id", ["lol-nami-wave", "ff3-tsunami"])
def test_wave_front_is_wider_across_than_along_its_direction(spell_id):
    spell = next(spell for spell in CATALOG if spell["id"] == spell_id)
    config = configuration(footprint_kind="rectangle", length_m=spell["length"], width_m=spell["width"])
    product = grid(config)
    shape = (64, 64)
    product = replace(product, elevation_m=np.zeros(shape), building_mask=np.zeros(shape, dtype=bool),
        sfincs_mask=np.ones(shape, dtype=np.uint8), width_cells=64, height_cells=64, x0_m=-32, y0_m=-32)
    source = resolve_rainfall(config)
    north, _ = magic_cell_rates(source, product)
    rows, columns = np.nonzero(north)
    assert np.ptp(columns) > np.ptp(rows)
    source.config = source.config.model_copy(update={"bearing_deg": 90})
    east, _ = magic_cell_rates(source, product)
    rows, columns = np.nonzero(east)
    assert np.ptp(rows) > np.ptp(columns)
