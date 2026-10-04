import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
import xarray as xr
from fastapi.testclient import TestClient
from pyproj import CRS, Transformer

from floodsim.domain.geometry import AnalysisArea, GeoBounds, LonLat
from floodsim.providers.common import local_crs
from floodsim.results.regular_flow import regular_flow_viewport
from floodsim.results.regular_netcdf_source import (
    inspect_regular_netcdf_source,
    regular_window_arrays,
)
from floodsim.results.regular_queries import (
    inspect_regular_point,
    prepare_regular_queries,
    saved_speed_scale,
)
from floodsim.results.vector_viewport import flow_vectors_viewport_geojson
from floodsim.results.view import PointOutsideResult, ResultTimeIndexInvalid


@pytest.fixture
def source_case(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()
    depth = np.full((3, 8, 8), 0.1, dtype=np.float32)
    depth[1, 3, 4] = 0.6
    depth[2, 3, 4] = 0.4
    maximum = depth.max(axis=0)
    maximum[3, 4] = np.nan
    active = np.ones((8, 8), dtype=np.int16)
    active[0, 0] = 0
    path = model / "sfincs_map.nc"
    xr.Dataset(
        {
            "h": (("time", "n", "m"), depth),
            "hmax": (("timemax", "n", "m"), maximum[None]),
            "zb": (("n", "m"), np.full((8, 8), 5, dtype=np.float32)),
            "msk": (("n", "m"), active),
            "u": (("time", "n", "m"), np.full_like(depth, 0.2)),
            "v": (("time", "n", "m"), np.full_like(depth, 0.1)),
        },
        coords={"time": [0, 60, 120], "timemax": [120]},
    ).to_netcdf(path)
    area = AnalysisArea(
        mode="rectangle",
        bounds=GeoBounds(
            west_deg=138.999, south_deg=34.999, east_deg=139.001, north_deg=35.001
        ),
        center=LonLat(lon_deg=139, lat_deg=35),
        width_m=8,
        height_m=8,
        area_m2=64,
    )
    source = inspect_regular_netcdf_source(
        path, model_dir=model, bounds=area.bounds.model_dump(), block_size_m=1
    )
    return replace(source, chunk_shape=(1, 4, 4)), model, area


def coordinates(area, x, y):
    return Transformer.from_crs(
        local_crs(area), CRS.from_epsg(4326), always_xy=True
    ).transform(x, y)


def test_point_summary_matches_native_series_and_survives_restart(
    source_case, monkeypatch
):
    source, model, area = source_case
    lon, lat = coordinates(area, 0.5, -0.5)
    before = inspect_regular_point(
        source, model_dir=model, area=area, lon=lon, lat=lat, time_index=2
    )
    root = prepare_regular_queries(source, model_dir=model)
    after = inspect_regular_point(
        source, model_dir=model, area=area, lon=lon, lat=lat, time_index=2
    )
    assert before == after
    assert after["max_time_index"] == 1
    assert after["max_depth_m"] == pytest.approx(0.6)
    assert after["depth_m"] == pytest.approx(0.4)
    assert after["speed_mps"] == pytest.approx(np.hypot(.2, .1))
    monkeypatch.setattr(
        xr,
        "open_dataset",
        lambda *a, **k: pytest.fail("summary must not reread NetCDF"),
    )
    assert prepare_regular_queries(source, model_dir=model) == root
    assert (
        inspect_regular_point(
            source, model_dir=model, area=area, lon=lon, lat=lat, time_index=None
        )["max_time_index"]
        == 1
    )


def test_point_rejects_outside_and_invalid_time_and_preserves_no_data(source_case):
    source, model, area = source_case
    with pytest.raises(PointOutsideResult):
        inspect_regular_point(
            source, model_dir=model, area=area, lon=140, lat=35, time_index=None
        )
    with pytest.raises(ResultTimeIndexInvalid):
        inspect_regular_point(
            source, model_dir=model, area=area, lon=139, lat=35, time_index=3
        )
    lon, lat = coordinates(area, -3.5, -3.5)
    for prepared in (False, True):
        if prepared:
            prepare_regular_queries(source, model_dir=model)
        point = inspect_regular_point(
            source, model_dir=model, area=area, lon=lon, lat=lat, time_index=1
        )
        assert not point["has_data"]
        assert point["max_time_index"] is None


def test_viewport_vectors_match_full_grid_without_losing_global_alignment(source_case):
    source, model, area = source_case
    prepare_regular_queries(source, model_dir=model)
    scale = saved_speed_scale(source, model)
    west, south = coordinates(area, -1.7, -1.7)
    east, north = coordinates(area, 2.7, 2.7)
    request = {
        "area": area,
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "stride": 2,
    }
    full = flow_vectors_viewport_geojson(
        regular_window_arrays(source, model_dir=model, time_index=2),
        time_index=0,
        speed_scale=scale,
        **request,
    )
    cropped = regular_flow_viewport(
        source, model_dir=model, time_index=2, speed_scale=scale, **request
    )
    for feature in full["features"]:
        feature["properties"]["time_index"] = 2
    assert cropped["features"] == full["features"]
    assert 0 < cropped["metadata"]["read_window_cells"] < 64


def test_half_metre_velocities_are_independent_of_two_metre_display_sampling(source_case):
    source, model, area = source_case
    area = area.model_copy(update={"width_m": 4, "height_m": 4, "area_m2": 16})
    source = replace(source, block_size_m=0.5)
    prepare_regular_queries(source, model_dir=model)
    west, south = coordinates(area, -3, -3)
    east, north = coordinates(area, 3, 3)
    data = regular_flow_viewport(source, model_dir=model, area=area, time_index=1,
        west=west, south=south, east=east, north=north, stride=2,
        speed_scale=saved_speed_scale(source, model), include_field=True)
    assert data["metadata"]["target_spacing_m"] == 2
    assert data["metadata"]["sample_stride_cells"] == 4
    assert len(data["features"]) == 4
    assert data["flow_field"]["cell_size_m"] == 0.5
    assert data["flow_field"]["width"] == data["flow_field"]["height"] == 8
    vectors = regular_flow_viewport(source, model_dir=model, area=area, time_index=1,
        west=west, south=south, east=east, north=north, stride=2,
        speed_scale=saved_speed_scale(source, model))
    assert "flow_field" not in vectors
    assert vectors["features"] == data["features"]


def test_source_api_queries_never_call_full_grid_adapter(source_case, monkeypatch):
    from floodsim.api import routes_results
    from floodsim.api.app import app

    source, model, area = source_case
    root = prepare_regular_queries(source, model_dir=model)
    descriptor = root.parent / "source.json"
    descriptor.write_text(json.dumps(source.to_json()), encoding="utf-8")
    monkeypatch.setattr(
        routes_results,
        "coordinator",
        SimpleNamespace(
            get=lambda run: SimpleNamespace(config=SimpleNamespace(analysis_area=area)),
            result_source_path=lambda run: descriptor,
            store=SimpleNamespace(run_dir=lambda run: model.parent),
        ),
    )
    monkeypatch.setattr(
        routes_results,
        "_source_arrays_for_run",
        lambda *args: pytest.fail("must not load full grid"),
    )
    client = TestClient(app)
    prefix = f"/api/v1/runs/{uuid4()}"
    lon, lat = coordinates(area, 0.5, -0.5)
    point = client.get(
        prefix + "/inspect", params={"lon": lon, "lat": lat, "time_index": 2}
    )
    assert point.status_code == 200
    assert point.json()["max_time_index"] == 1
    flow = client.get(
        prefix + "/layers/flow-vectors.geojson",
        params={
            "time_index": 2,
            "west": 138.999,
            "south": 34.999,
            "east": 139.001,
            "north": 35.001,
            "stride": 2,
        },
    )
    assert flow.status_code == 200
    assert flow.json()["features"]
    monkeypatch.setattr(routes_results, "_arrays_for_run",
                        lambda *args: pytest.fail("elevation must not load water or velocity"))
    elevation = client.get(prefix + "/layers/elevation.png")
    assert elevation.status_code == 200
    assert elevation.content.startswith(b"\x89PNG")
    assert client.get(prefix + "/layers/elevation.png").content == elevation.content
    from floodsim.results import regular_queries

    regular_queries._elevation_cached.cache_clear()
    monkeypatch.setattr(
        regular_queries, "render_elevation_values_png",
        lambda *args, **kwargs: pytest.fail("saved elevation PNG must survive cache reset"),
    )
    assert client.get(prefix + "/layers/elevation.png").content == elevation.content
    grid = client.get(prefix + "/layers/grid-resolution.png")
    assert grid.status_code == 200
    assert grid.content.startswith(b"\x89PNG")
    regular_queries._grid_cached.cache_clear()
    monkeypatch.setattr(
        regular_queries, "render_regular_grid_resolution_png",
        lambda *args, **kwargs: pytest.fail("saved grid PNG must survive cache reset"),
    )
    assert client.get(prefix + "/layers/grid-resolution.png").content == grid.content


def test_regular_grid_reads_only_mask(source_case, monkeypatch):
    from floodsim.results import regular_queries
    from floodsim.results.view import render_grid_resolution_png

    source, model, _ = source_case
    expected = render_grid_resolution_png(regular_window_arrays(source, model_dir=model, time_index=0))
    original_open = xr.open_dataset

    def mask_only(path):
        with original_open(path) as dataset:
            return dataset[["msk"]].load()

    monkeypatch.setattr(regular_queries.xr, "open_dataset", mask_only)
    assert regular_queries.regular_grid_png(source, model, 4096) == expected


def test_half_metre_grid_has_visible_cells(source_case):
    from io import BytesIO

    from PIL import Image

    from floodsim.results.regular_queries import regular_grid_png

    source, model, _ = source_case
    source = replace(source, block_size_m=0.5)
    rgba = np.array(Image.open(BytesIO(regular_grid_png(source, model, 4096))))
    assert np.any(rgba[..., 3] > 0)


@pytest.mark.parametrize("layer", ["grid_resolution", "elevation"])
def test_legacy_static_layers_do_not_decode_water_or_velocity(tmp_path, monkeypatch, layer):
    from floodsim.results import view

    archive_path = tmp_path / "static.npz"
    np.savez(archive_path, active_mask=np.ones((2, 2), dtype=bool),
             terrain_elevation_m=np.ones((2, 2), dtype=np.float32), grid_resolution_m=0.5)
    original_load = np.load

    class StaticArchive:
        def __init__(self, archive):
            self.archive = archive
            self.files = archive.files
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.archive.close()
        def __getitem__(self, name):
            assert name not in {"depth_time_m", "max_depth_m", "velocity_u_mps", "velocity_v_mps", "time_values"}
            if layer == "grid_resolution":
                assert name != "terrain_elevation_m"
            return self.archive[name]

    monkeypatch.setattr(view.np, "load", lambda *args, **kwargs: StaticArchive(original_load(*args, **kwargs)))
    arrays = view.load_normalized_arrays(archive_path, static_layer=layer)
    png = view.render_grid_resolution_png(arrays) if layer == "grid_resolution" else view.render_terrain_elevation_png(arrays)
    assert png.startswith(b"\x89PNG")


def test_depth_reads_only_selected_h_and_reuses_global_scale(source_case, monkeypatch):
    from floodsim.results import regular_queries
    from floodsim.results.view import render_time_depth_png

    source, model, _ = source_case
    prepare_regular_queries(source, model_dir=model)
    expected = render_time_depth_png(regular_window_arrays(source, model_dir=model, time_index=1), time_index=0)
    original_open = xr.open_dataset
    reads = []
    def depth_only(path):
        with original_open(path) as dataset:
            reads.append(path)
            return dataset[["h"]].load()
    monkeypatch.setattr(regular_queries.xr, "open_dataset", depth_only)
    actual = regular_queries.regular_depth_png(source, model, 1, 4096)
    assert actual == expected
    assert len(reads) == 1
    assert regular_queries.regular_depth_png(source, model, 1, 4096) == actual
    assert len(reads) == 1
