import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
import xarray as xr
from fastapi.testclient import TestClient

from floodsim.domain.geometry import AnalysisArea, GeoBounds, LonLat
from floodsim.results.extrema import array_extrema, regular_extrema
from floodsim.results.outflow_energy import regular_energy
from floodsim.results.regular_netcdf_source import inspect_regular_netcdf_source
from floodsim.results.view import AdaptiveNormalizedArrays, NormalizedArrays


@pytest.fixture
def extrema_case(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    depth = np.ones((3, 4, 4), dtype=np.float32)
    depth[1, 2, 2], depth[2, 2, 2] = 7, 4
    depth[:, 0, 0] = 0  # Dry velocities must not enter the speed ranking.
    active = np.ones((4, 4), dtype=np.int16)
    active[0, 1] = 0
    u = np.ones_like(depth)
    u[:, 0, :2] = 999
    u[1, 1, 2], u[2, 1, 2] = 20, 6
    u[2, 2, 2] = np.nan
    v = np.zeros_like(depth)
    path = model / "sfincs_map.nc"
    xr.Dataset(
        {
            "h": (("time", "n", "m"), depth),
            "hmax": (("timemax", "n", "m"), np.full((1, 4, 4), 999)),
            "u": (("time", "n", "m"), u),
            "v": (("time", "n", "m"), v),
            "msk": (("n", "m"), active),
            "zb": (("n", "m"), np.zeros((4, 4))),
        },
        coords={"time": [0, 1, 2], "timemax": [2]},
    ).to_netcdf(path)
    area = AnalysisArea(
        mode="rectangle",
        center=LonLat(lon_deg=139, lat_deg=35),
        bounds=GeoBounds(
            west_deg=138.999, east_deg=139.001, south_deg=34.999, north_deg=35.001
        ),
        width_m=2,
        height_m=2,
        area_m2=4,
    )
    source = replace(
        inspect_regular_netcdf_source(
            path, model_dir=model, bounds=area.bounds.model_dump(), block_size_m=0.5
        ),
        chunk_shape=(1, 2, 2),
    )
    arrays = NormalizedArrays(
        depth,
        depth.max(axis=0),
        np.zeros((4, 4)),
        active > 0,
        source.time_values,
        0.5,
        u,
        v,
    )
    return source, model, area, arrays


def test_extrema_match_native_arrays_and_retained_output_times(extrema_case):
    source, model, area, arrays = extrema_case
    payload = regular_extrema(source, model_dir=model, area=area, times=[0, 2])
    assert payload == array_extrema(arrays, area=area, times=[0, 2])
    deepest, fastest = payload["depth"][0], payload["speed"][0]
    assert (deepest["depth_m"], deepest["time_index"], deepest["cell_index"]) == (
        4,
        2,
        10,
    )
    assert (fastest["speed_mps"], fastest["time_index"], fastest["cell_index"]) == (
        6,
        2,
        6,
    )
    assert deepest["cell_area_m2"] == 0.25
    for entries in payload.values():
        assert len(entries) == 10
        assert len({entry["cell_index"] for entry in entries}) == 10
        assert [entry["rank"] for entry in entries] == list(range(1, 11))
        assert all(entry["time_index"] in [0, 2] for entry in entries)
    assert payload["depth"][1]["time_index"] == 0  # Earliest equal maximum.
    all_times = regular_extrema(source, model_dir=model, area=area, times=[0, 1, 2])
    assert all_times["depth"][0]["depth_m"] == 7
    assert all_times["speed"][0]["speed_mps"] == 20


def test_extrema_reads_only_2d_chunks_and_reuses_persisted_summary(
    extrema_case, monkeypatch
):
    source, model, area, _ = extrema_case
    original = xr.DataArray.values.fget
    reads = []

    def bounded_values(array):
        reads.append((array.name, array.shape))
        assert array.ndim == 2
        assert array.size <= 4
        assert array.name in {"h", "u", "v", "msk"}
        return original(array)

    monkeypatch.setattr(xr.DataArray, "values", property(bounded_values))
    payload = regular_extrema(source, model_dir=model, area=area, times=[0, 2])
    assert reads
    monkeypatch.setattr(
        xr,
        "open_dataset",
        lambda *args, **kwargs: pytest.fail("cached ranking must not reopen NetCDF"),
    )
    assert regular_extrema(source, model_dir=model, area=area, times=[0, 2]) == payload


def test_ranking_preserves_storage_chunks_instead_of_repeating_decompression(
    extrema_case, monkeypatch
):
    source, model, area, arrays = extrema_case
    source = replace(source, chunk_shape=(1, 4, 4))
    original = xr.DataArray.values.fget
    reads = []

    def frame_values(array):
        reads.append((array.name, array.shape))
        assert array.ndim == 2
        assert array.shape == (4, 4)
        return original(array)

    monkeypatch.setattr(xr.DataArray, "values", property(frame_values))
    assert regular_extrema(source, model_dir=model, area=area, times=[0, 1, 2]) == array_extrema(
        arrays, area=area, times=[0, 1, 2]
    )
    assert len(reads) == 10  # One mask plus h/u/v once each per output frame.


def test_extrema_progress_counts_chunk_frames_and_cache_skips_reads(extrema_case, monkeypatch):
    source, model, area, arrays = extrema_case
    progress = []
    regular = regular_extrema(source, model_dir=model, area=area, times=[0, 2],
                              progress=lambda done, total: progress.append((done, total)))
    assert progress == [(done, 8) for done in range(9)]
    progress.clear()
    assert array_extrema(arrays, area=area, times=[0, 2],
                         progress=lambda done, total: progress.append((done, total))) == regular
    assert progress == [(0, 2), (1, 2), (2, 2)]
    monkeypatch.setattr(xr, "open_dataset", lambda *args, **kwargs: pytest.fail("cache must not read NetCDF"))
    progress.clear()
    assert regular_extrema(source, model_dir=model, area=area, times=[0, 2],
                           progress=lambda done, total: progress.append((done, total))) == regular
    assert progress == [(8, 8)]


def test_extrema_jobs_progress_deduplication_and_failure():
    from threading import Event

    from floodsim.results.energy_jobs import EnergyJobs

    jobs = EnergyJobs(result_fields=("depth", "speed"), failure_message="集計失敗")
    reached, release = Event(), Event()

    def calculate(progress):
        progress(4, 8)
        reached.set()
        assert release.wait(5)
        progress(8, 8)
        assert jobs.get("run")["progress_percent"] == 99
        return {"depth": [], "speed": []}

    try:
        queued = jobs.start("run", 0, calculate)
        assert queued["status"] == "queued"
        assert reached.wait(5)
        state = jobs.get("run")
        assert state["progress_percent"] == 50
        assert state["processed_frames"] == 4
        assert state["total_frames"] == 8
        assert jobs.start("run", 0, lambda progress: pytest.fail("duplicate calculation")) == state
    finally:
        release.set()
        jobs.close()
    assert jobs.get("run")["progress_percent"] == 100
    assert jobs.get("run")["status"] == "complete"

    failed_jobs = EnergyJobs(result_fields=("depth", "speed"), failure_message="集計失敗")

    def fail(progress):
        raise RuntimeError("internal")

    failed_jobs.start("bad", 0, fail)
    failed_jobs.close()
    assert failed_jobs.get("bad")["status"] == "failed"
    assert failed_jobs.get("bad")["progress_percent"] == 0
    assert failed_jobs.get("bad")["error"] == "集計失敗"


def test_empty_and_no_velocity_results(extrema_case):
    source, model, area, arrays = extrema_case
    assert regular_extrema(source, model_dir=model, area=area, times=[]) == {
        "depth": [],
        "speed": [],
    }
    no_velocity = replace(arrays, velocity_u_mps=None, velocity_v_mps=None)
    payload = array_extrema(no_velocity, area=area, times=[0, 1, 2])
    assert payload["speed"] == []
    assert payload["depth"][0]["speed_mps"] is None
    dry = replace(arrays, depth_time_m=np.zeros_like(arrays.depth_time_m))
    assert array_extrema(dry, area=area, times=[0, 1, 2]) == {"depth": [], "speed": []}


def test_adaptive_face_coordinates_and_area(extrema_case):
    _, _, area, _ = extrema_case
    arrays = AdaptiveNormalizedArrays(
        np.array([[1, 2], [3, 1]]),
        np.array([3, 2]),
        np.zeros(2),
        np.ones(2, dtype=bool),
        ("0", "1"),
        np.array([1, 2]),
        np.array([0, 1]),
        np.array([0, 1]),
        np.array([1, 4]),
        4,
        4,
        np.array([[1, 2], [3, 1]]),
        np.zeros((2, 2)),
    )
    result = array_extrema(arrays, area=area, times=[0, 1])
    assert result["depth"][0]["time_index"] == 1
    assert result["depth"][1]["cell_area_m2"] == 4
    assert result["depth"][1]["lon_deg"] > result["depth"][0]["lon_deg"]


def test_api_extrema_does_not_load_full_source_grid(extrema_case, monkeypatch):
    from floodsim.api import routes_results
    from floodsim.api.app import app
    from floodsim.results.energy_jobs import EnergyJobs

    source, model, area, _ = extrema_case
    descriptor = model.parent / "source.json"
    descriptor.write_text(json.dumps(source.to_json()), encoding="utf-8")
    monkeypatch.setattr(
        routes_results,
        "coordinator",
        SimpleNamespace(
            get=lambda run: SimpleNamespace(config=SimpleNamespace(analysis_area=area)),
            result_metadata=lambda run: {
                "available_time_indices": [0, 2],
                "time_values": ["0", "1", "2"],
            },
            result_source_path=lambda run: descriptor,
            store=SimpleNamespace(run_dir=lambda run: model.parent),
        ),
    )
    monkeypatch.setattr(
        routes_results,
        "_arrays_for_run",
        lambda *args: pytest.fail("no full-grid adapter"),
    )
    client = TestClient(app)
    url = f"/api/v1/runs/{uuid4()}/result-extrema"
    jobs = EnergyJobs(result_fields=("depth", "speed"))
    monkeypatch.setattr(routes_results, "_extrema_jobs", jobs)
    assert client.get(url + "/progress").status_code == 404
    try:
        queued = client.post(url)
        assert queued.status_code == 200
        assert queued.json()["status"] == "queued"
    finally:
        jobs.close()
    status = client.get(url + "/progress").json()
    assert status["status"] == "complete"
    assert status["progress_percent"] == 100
    assert status["processed_frames"] == status["total_frames"] == 8
    assert client.post(url).json() == status
    response = client.get(url)
    assert response.status_code == 200
    assert response.json()["speed"][0]["speed_mps"] == 6
    assert response.json() == {"depth": status["depth"], "speed": status["speed"]}


def test_energy_integrates_one_metre_boundaries_and_caches_only_final_ranking(extrema_case, monkeypatch):
    source, model, area, _ = extrema_case
    path = model / "sfincs_map.nc"
    with xr.open_dataset(path) as dataset:
        uniform = dataset.load()
    uniform["h"].values[:] = 1
    uniform["u"].values[:] = 2
    uniform["v"].values[:] = 0
    uniform["msk"].values[:] = 1
    uniform.to_netcdf(path)
    source = inspect_regular_netcdf_source(path, model_dir=model, bounds=area.bounds.model_dump(), block_size_m=.5)
    original = xr.DataArray.values.fget
    reads = []

    def bounded_values(array):
        reads.append((array.name, array.shape))
        assert array.ndim <= 2
        return original(array)

    monkeypatch.setattr(xr.DataArray, "values", property(bounded_values))
    progress = []
    payload = regular_energy(source, model_dir=model, area=area, times=[0, 2],
                             progress=lambda done, total: progress.append((done, total)))
    assert progress == [(1, 2), (2, 2)]
    assert len(payload["energy"]) == 2
    assert payload["aggregation_buffer_bytes"] == 192
    for rank, entry in enumerate(payload["energy"], 1):
        assert entry["rank"] == rank
        assert entry["total_energy_j"] == pytest.approx(8000)
        assert entry["total_outflow_m3"] == pytest.approx(4)
        assert entry["time_index"] == 2
        assert entry["cell_area_m2"] == 1
    assert len([r for r in reads if r[0] in ("h", "u", "v")]) == 6
    assert not list(model.parent.rglob("frame-*.npz"))
    monkeypatch.setattr(xr, "open_dataset", lambda *args, **kwargs: pytest.fail("cached energy must not reopen source"))
    assert regular_energy(source, model_dir=model, area=area, times=[0, 2], progress=lambda *args: None) == payload


def test_energy_api_returns_actual_progress_and_reuses_completed_job(extrema_case, monkeypatch):
    from threading import Event

    from floodsim.api import routes_results
    from floodsim.api.app import app
    from floodsim.results.energy_jobs import EnergyJobs

    source, model, area, _ = extrema_case
    descriptor = model.parent / "source.json"
    descriptor.write_text(json.dumps(source.to_json()), encoding="utf-8")
    monkeypatch.setattr(routes_results, "coordinator", SimpleNamespace(
        get=lambda run: SimpleNamespace(config=SimpleNamespace(analysis_area=area)),
        result_metadata=lambda run: {"available_time_indices": [0, 1, 2]},
        result_source_path=lambda run: descriptor,
        store=SimpleNamespace(run_dir=lambda run: model.parent)))
    jobs = EnergyJobs()
    monkeypatch.setattr(routes_results, "_energy_jobs", jobs)
    ready, release = Event(), Event()
    calls = []

    def calculate(*args, progress, **kwargs):
        calls.append(1)
        progress(1, 3)
        ready.set()
        assert release.wait(5)
        progress(3, 3)
        return {"energy": [], "aggregation_buffer_bytes": 192,
                "_totals": np.array([np.ones((2, 2)), [[4., 3.], [2., 1.]]])}

    monkeypatch.setattr(routes_results, "regular_energy", calculate)
    client = TestClient(app)
    url = f"/api/v1/runs/{uuid4()}/result-energy"
    try:
        assert client.get(url).status_code == 404
        assert client.post(url).json()["status"] == "queued"
        assert ready.wait(5)
        state = client.get(url).json()
        assert state["status"] == "running"
        assert state["progress_percent"] == 33
        assert state["processed_frames"] == 1
        assert client.post(url).json()["progress_percent"] == 33
        assert client.get(url + "/point?lon=139&lat=35").status_code == 409
    finally:
        release.set()
        jobs.close()
    assert client.get(url).json()["progress_percent"] == 100
    assert client.post(url).json()["status"] == "complete"
    assert "_totals" not in client.get(url).json()
    monkeypatch.setattr(xr, "open_dataset", lambda *args, **kwargs: pytest.fail("point energy must use retained memory"))
    point = client.get(url + "/point?lon=139&lat=35")
    assert point.status_code == 200
    assert point.json()["total_energy_j"] == 1
    assert point.json()["total_outflow_m3"] == 1
    assert point.json()["rank"] == 4
    assert point.json()["through_time_index"] == 2
    assert client.get(url + "/point?lon=140&lat=35").status_code == 404
    assert calls == [1]


def test_memory_energy_points_include_locations_below_top_ten_and_zero_flow():
    from floodsim.results.energy_jobs import EnergyJobs

    jobs = EnergyJobs()
    totals = np.array([np.ones((4, 4)), np.arange(1, 17).reshape(4, 4)], dtype=float)
    totals[1, 0, 0] = np.nan
    totals[1, 0, 1] = 0
    jobs.start("test", 1, lambda progress: {"energy": [], "_totals": totals})
    jobs.close()
    assert jobs.point("test", 1, 1) == {"has_data": True, "total_energy_j": 6., "total_outflow_m3": 1., "rank": 11}
    assert jobs.point("test", 0, 0) == {"has_data": False}
    assert jobs.point("test", 0, 1)["rank"] is None
    assert jobs.point("missing", 0, 0) is None
    with pytest.raises(ValueError):
        jobs.point("test", 4, 0)


def test_retained_energy_keeps_the_aggregate_buffer_after_work_is_released(extrema_case):
    source, model, area, _ = extrema_case
    result = regular_energy(source, model_dir=model, area=area, times=[0, 1, 2],
                            progress=lambda *args: None, retain_totals=True)
    assert result["_totals"].shape == (2, 2, 2)
    for entry in result["energy"]:
        assert result["_totals"][1].ravel()[entry["cell_index"]] == entry["total_energy_j"]
    assert result["_totals"].nbytes == 64
