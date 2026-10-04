from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from floodsim.results.regular_netcdf_source import (
    RegularNetcdfSourceError,
    inspect_regular_netcdf_source,
    read_regular_point_depth_series,
    regular_speed_reference,
    regular_window_arrays,
    scan_regular_diagnostics,
    validate_source_identity,
)


def test_source_reference_uses_wet_peak_speed_across_chunks_and_frames(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    path = model / "sfincs_map.nc"
    _write_result(path)
    with xr.open_dataset(path) as original:
        dataset = original.load()
    dataset["u"] = (
        ("time", "n", "m"),
        np.array(
            [[[1000, 0.2], [0.1, 1000]], [[1000, 0.4], [1.0, 1000]]], dtype=np.float32
        ),
    )
    dataset["v"] = (("time", "n", "m"), np.zeros((2, 2, 2), dtype=np.float32))
    dataset["hmax"].values[0, 1, 0] = np.nan
    dataset.to_netcdf(path)
    source = inspect_regular_netcdf_source(
        path,
        model_dir=model,
        bounds={"west": 0, "south": 0, "east": 1, "north": 1},
        block_size_m=2.0,
    )
    source = replace(source, chunk_shape=(1, 1, 1))
    scale = regular_speed_reference(source, model_dir=model)
    assert scale.maximum == 1
    assert scale.active_area == 8
    assert scale.class_count == 2
    first = regular_window_arrays(source, model_dir=model, time_index=0)
    second = regular_window_arrays(source, model_dir=model, time_index=1)
    assert first.max_depth_m[1, 0] == pytest.approx(0.2)
    assert first.depth_scale.breaks == second.depth_scale.breaks


def _write_result(path: Path) -> None:
    depth = np.asarray(
        [
            [[0.0, 0.2], [0.1, 0.0]],
            [[0.0, 0.3], [0.2, 0.0]],
        ],
        dtype=np.float32,
    )
    xr.Dataset(
        {
            "h": (("time", "n", "m"), depth),
            "hmax": (
                ("timemax", "n", "m"),
                np.asarray([[[np.nan, 0.3], [0.2, np.nan]]], dtype=np.float32),
            ),
            "zb": (("n", "m"), np.zeros((2, 2), dtype=np.float32)),
            "msk": (("n", "m"), np.ones((2, 2), dtype=np.int16)),
        },
        coords={"time": [0, 60], "timemax": [60]},
    ).to_netcdf(path)


def test_magic_submillimetre_depth_is_visible_and_threshold_is_portable(tmp_path: Path) -> None:
    from io import BytesIO

    from PIL import Image

    from floodsim.results.regular_netcdf_source import load_regular_netcdf_descriptor
    from floodsim.results.view import render_max_depth_png

    model = tmp_path / "model"
    model.mkdir()
    path = model / "sfincs_map.nc"
    _write_result(path)
    with xr.open_dataset(path) as original:
        dataset = original.load()
    dataset["h"].values[:] *= 0.0001
    dataset["hmax"].values[:] *= 0.0001
    dataset.to_netcdf(path)
    rain = inspect_regular_netcdf_source(path, model_dir=model,
        bounds={"west": 0, "south": 0, "east": 1, "north": 1}, block_size_m=1)
    magic = replace(rain, display_dry_threshold_m=1e-6)
    descriptor = tmp_path / "source.json"
    import json
    descriptor.write_text(json.dumps(magic.to_json()), encoding="utf-8")
    restored = load_regular_netcdf_descriptor(descriptor)
    assert restored.display_dry_threshold_m == 1e-6
    diagnostic = scan_regular_diagnostics(restored, model_dir=model)
    assert diagnostic["min_visible_depth_m"] < .001
    rain_image = Image.open(BytesIO(render_max_depth_png(regular_window_arrays(rain, model_dir=model, time_index=0))))
    magic_image = Image.open(BytesIO(render_max_depth_png(regular_window_arrays(restored, model_dir=model, time_index=0))))
    assert np.asarray(rain_image)[..., 3].max() == 0
    assert np.asarray(magic_image)[..., 3].max() > 0


def test_descriptor_is_relative_and_diagnostics_are_chunk_bounded(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    source_path = model / "sfincs_map.nc"
    _write_result(source_path)

    source = inspect_regular_netcdf_source(
        source_path,
        model_dir=model,
        bounds={"west": 139.0, "south": 35.0, "east": 139.1, "north": 35.1},
        block_size_m=1.0,
    )

    assert source.source_filename == "sfincs_map.nc"
    assert source.time_values == ("0", "60")
    assert source.to_json()["schema_version"] == "regular-netcdf-source-v1"
    assert scan_regular_diagnostics(source, model_dir=model)[
        "global_max_depth_m"
    ] == pytest.approx(0.3)
    assert read_regular_point_depth_series(
        source,
        model_dir=model,
        row=0,
        column=1,
    ).tolist() == pytest.approx([0.2, 0.3])


def test_descriptor_rejects_source_outside_model_and_identity_change(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    external = tmp_path / "sfincs_map.nc"
    _write_result(external)
    with pytest.raises(RegularNetcdfSourceError, match="below the model"):
        inspect_regular_netcdf_source(
            external,
            model_dir=model,
            bounds={"west": 0, "south": 0, "east": 1, "north": 1},
            block_size_m=1.0,
        )

    source_path = model / "sfincs_map.nc"
    _write_result(source_path)
    source = inspect_regular_netcdf_source(
        source_path,
        model_dir=model,
        bounds={"west": 0, "south": 0, "east": 1, "north": 1},
        block_size_m=1.0,
    )
    source_path.write_bytes(source_path.read_bytes() + b"changed")
    with pytest.raises(RegularNetcdfSourceError, match="identity"):
        validate_source_identity(source, model_dir=model)
