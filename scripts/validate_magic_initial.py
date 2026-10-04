"""Real Galibier proof of restart water, face ordering and vortex handedness."""
from __future__ import annotations

import json
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
    root = Path("artifacts/magic-initial-momentum")
    area = AnalysisArea(mode="rectangle", bounds=GeoBounds(west_deg=138.999, south_deg=34.999,
        east_deg=139.001, north_deg=35.001), center=LonLat(lon_deg=139, lat_deg=35),
        width_m=20, height_m=20, area_m2=400)
    shape = (40, 40)
    buildings = np.zeros(shape, dtype=bool)
    product = FullGridProduct(np.zeros(shape, dtype=np.float32), buildings,
        np.ones(shape, dtype=np.uint8), np.full(shape, .03, dtype=np.float32),
        np.ones(shape, dtype=np.float32), allocate_roof_rainfall(buildings, cell_area_m2=.25),
        40, 40, .5, .5, -10, -10, local_crs(area).to_wkt())
    reports = []
    for name, motion, bearing, rotation, footprint in [
        ("still", "none", 0, "clockwise", "circle"), ("east", "directional", 90, "clockwise", "circle"),
        ("north", "directional", 0, "clockwise", "circle"), ("radial", "radial", 0, "clockwise", "circle"),
        ("clockwise", "vortex", 0, "clockwise", "circle"), ("counterclockwise", "vortex", 0, "counterclockwise", "circle"),
        ("band", "directional", 90, "clockwise", "rectangle"),
        ("sector", "radial", 0, "clockwise", "sector"),
        ("domain", "directional", 90, "clockwise", "domain"),
    ]:
        magic = WaterMagicConfig(position=area.center, radius_m=3, bearing_deg=bearing,
            footprint_kind=footprint, length_m=6, width_m=4,
            volume_m3=20, casting_seconds=2, relaxation_seconds=0, release_mode="initial",
            initial_motion=motion, initial_speed_mps=0 if motion == "none" else 2,
            vortex_direction=rotation, vortex_core_radius_m=1.5)
        config = RunConfig(analysis_area=area, requested_accuracy_mode=AccuracyMode.FULL_1M,
            grid_cell_size_m=.5, water_magic=magic)
        build = SfincsModelBuilder().build(root / name / "model", product, resolve_rainfall(config))
        run = SfincsRunner().run(build.model_dir, logs_dir=root / name / "logs", engine=engine)
        with xr.open_dataset(run.result_path) as ds:
            h, u, v = (np.nan_to_num(ds[key].values) for key in ("h", "u", "v"))
            x, y = np.meshgrid(np.arange(40) * .5 - 9.75, np.arange(40) * .5 - 9.75)
            inner = x*x + y*y < 4
            volume = h.sum(axis=(1, 2)) * .25
            rotation_score = float(np.mean((x*v[0]-y*u[0])[inner]))
            radial_score = float(np.mean((x*u[0]+y*v[0])[inner]))
            report = {"fixture":name, "initial_volume_m3":float(volume[0]), "final_volume_m3":float(volume[-1]),
                "mean_initial_u":float(u[0][inner].mean()), "mean_initial_v":float(v[0][inner].mean()),
                "rotation_score":rotation_score, "radial_score":radial_score,
                "initial_max_speed_mps":float(np.hypot(u[0], v[0]).max()),
                "elapsed_seconds":((ds.time.values-ds.time.values[0])/np.timedelta64(1, "s")).tolist(),
                "model_report":build.report}
            reports.append(report)
            evidence = {key: value for key, value in report.items() if key != "model_report"}
            assert np.allclose(volume, 20, rtol=.01), evidence
            if name == "east":
                assert report["mean_initial_u"] > 1.9 and abs(report["mean_initial_v"]) < .001, evidence
            if name == "north":
                assert report["mean_initial_v"] > 1.9 and abs(report["mean_initial_u"]) < .001, evidence
            if name == "radial":
                assert radial_score > 1, evidence
            if name == "clockwise":
                assert rotation_score < -1, evidence
            if name == "counterclockwise":
                assert rotation_score > 1, evidence
            if name == "still":
                assert report["initial_max_speed_mps"] < 1e-5, evidence
        print(json.dumps({key: value for key, value in report.items() if key != "model_report"}))
    (root / "engine_validation.json").write_text(json.dumps({"engine":str(engine.executable),
        "sha256":engine.sha256, "grid_m":.5, "reports":reports}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
