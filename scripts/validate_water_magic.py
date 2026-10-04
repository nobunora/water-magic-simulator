"""Tiny permitted-engine proof of second outputs, mass and zero-source control."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import xarray as xr

from floodsim.domain.geometry import AnalysisArea, GeoBounds, LonLat
from floodsim.domain.run_config import AccuracyMode, RunConfig
from floodsim.domain.water_magic import WaterMagicConfig
from floodsim.orchestration.rainfall_resolution import resolve_rainfall
from floodsim.preprocessing.full_grid import FullGridProduct
from floodsim.preprocessing.roof_rainfall import allocate_roof_rainfall
from floodsim.providers.common import local_crs
from floodsim.sfincs.model_builder import SfincsModelBuilder
from floodsim.sfincs.runner import SfincsRunner, resolve_sfincs_executable
from scripts.run_local_review import _configure_sfincs


def main() -> None:
    _configure_sfincs(None)
    engine = resolve_sfincs_executable()
    root = Path("artifacts/water-magic-validation")
    area = AnalysisArea(mode="rectangle", bounds=GeoBounds(west_deg=138.999, south_deg=34.999,
        east_deg=139.001, north_deg=35.001), center=LonLat(lon_deg=139, lat_deg=35),
        width_m=32, height_m=32, area_m2=1024)
    shape = (32, 32)
    ground = np.zeros(shape, dtype=bool)
    allocation = allocate_roof_rainfall(ground, active_mask=np.ones(shape, dtype=bool), cell_area_m2=1.0)
    grid = FullGridProduct(np.zeros(shape, dtype=np.float32), ground,
        np.ones(shape, dtype=np.uint8), np.full(shape, 0.03, dtype=np.float32),
        np.ones(shape, dtype=np.float32), allocation, 32, 32, 1, 1, -16, -16,
        local_crs(area).to_wkt())
    magic = WaterMagicConfig(position=area.center, radius_m=3, bearing_deg=0,
        volume_m3=1, casting_seconds=5, relaxation_seconds=60)
    config = RunConfig(analysis_area=area, requested_accuracy_mode=AccuracyMode.FULL_1M,
        grid_cell_size_m=1, water_magic=magic)
    reports = []
    for name, elevation, run_config in (("flat", grid.elevation_m - 1.0, config),
        ("slope", np.tile(np.arange(32) * 0.002 - .02, (32, 1)).astype(np.float32), config),
        ("domain", grid.elevation_m, config.model_copy(update={"water_magic": magic.model_copy(update={"footprint_kind": "domain"})})),
        ("long", grid.elevation_m, config.model_copy(update={"water_magic": magic.model_copy(update={"casting_seconds": 180, "relaxation_seconds": 180})}))):
        build = SfincsModelBuilder().build(root / name / "model", replace(grid, elevation_m=elevation), resolve_rainfall(run_config))
        run = SfincsRunner().run(build.model_dir, logs_dir=root / name / "logs", engine=engine)
        with xr.open_dataset(run.result_path) as ds:
            times = ((ds.time.values - ds.time.values[0]) / np.timedelta64(1, "s")).tolist()
            depth = ds["h"].values
            volumes = np.nansum(depth, axis=(1, 2))
            reports.append({"fixture": name, "elapsed_seconds": times,
                "stored_volume_m3": volumes.tolist(), "requested_volume_m3": 1,
                "relative_mass_error": abs(float(volumes[-1]) - 1),
                "model_report": build.report})
        if name == "flat":
            control_root = root / "zero" / "model"
            shutil.copytree(build.model_dir, control_root, dirs_exist_ok=True)
            flows = np.loadtxt(control_root / "water_magic.dis")
            flows[:, 1:] = 0
            np.savetxt(control_root / "water_magic.dis", flows)
            control_run = SfincsRunner().run(control_root, logs_dir=root / "zero" / "logs", engine=engine)
            with xr.open_dataset(control_run.result_path) as ds:
                reports.append({"fixture": "zero", "maximum_depth_m": float(np.nan_to_num(ds["h"].values, nan=0).max())})
    result = {"engine": str(engine.executable), "engine_sha256": engine.sha256, "reports": reports}
    (root / "engine_validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"engine": result["engine"], "engine_sha256": engine.sha256,
        "fixtures": [{k: v for k, v in r.items() if k not in {"model_report", "stored_volume_m3", "elapsed_seconds"}} for r in reports],
        "times": reports[0]["elapsed_seconds"]}, indent=2))
    assert all(r.get("relative_mass_error", 0) <= .01 for r in reports)
    assert reports[1]["maximum_depth_m"] == 0
    assert np.allclose(reports[0]["elapsed_seconds"], np.arange(0, 65.0001, .2))


if __name__ == "__main__":
    main()
