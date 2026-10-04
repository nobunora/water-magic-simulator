"""Run all catalog presets with the permitted local SFINCS executable."""
from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid-m", type=float, choices=(0.5, 1), default=1)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--initial", action="store_true", help="Use the current initial-placement presets")
    parser.add_argument("--spell-id", action="append", help="Limit this validation to selected catalog IDs")
    args = parser.parse_args()
    grid_m = args.grid_m
    _configure_sfincs(None)
    engine = resolve_sfincs_executable()
    root = Path("artifacts/water-magic-maximum-validation")
    if grid_m == 0.5:
        root = Path("artifacts/water-magic-half-metre-catalog-validation")
    root = args.output_dir or root
    catalog = json.loads(Path("web/src/dev/magicCatalog.json").read_text(encoding="utf-8"))
    catalog = [spell for spell in catalog if spell["maxVolume"] > 5]
    area = AnalysisArea(mode="rectangle", bounds=GeoBounds(west_deg=138.999, south_deg=34.999,
        east_deg=139.001, north_deg=35.001), center=LonLat(lon_deg=139, lat_deg=35),
        width_m=200, height_m=200, area_m2=40000)
    cells = round(200 / grid_m)
    shape = (cells, cells)
    ground = np.zeros(shape, dtype=bool)
    product = FullGridProduct(np.zeros(shape, dtype=np.float32), ground,
        np.ones(shape, dtype=np.uint8), np.full(shape, .03, dtype=np.float32),
        np.ones(shape, dtype=np.float32), allocate_roof_rainfall(ground, cell_area_m2=grid_m**2),
        cells, cells, grid_m, grid_m, -100, -100, local_crs(area).to_wkt())
    reports = []
    fixtures = [*catalog, {**next(spell for spell in catalog if spell["id"]=="ff7-tidalwave"), "variant":"minimum", "maxVolume":1000},
        {**next(spell for spell in catalog if spell["id"]=="chrono-water2"), "variant":"long", "casting":300}]
    if args.spell_id:
        unknown = set(args.spell_id) - {spell["id"] for spell in catalog}
        if unknown:
            parser.error(f"Unknown catalog IDs: {sorted(unknown)}")
        fixtures = [spell for spell in fixtures if spell["id"] in args.spell_id and "variant" not in spell]
    for spell in fixtures:
        name = spell["id"] + ("-"+spell["variant"] if "variant" in spell else "")
        magic = WaterMagicConfig(spell_id=spell["id"], position=area.center,
            footprint_kind=spell["kind"], radius_m=spell["radius"], length_m=spell["length"],
            width_m=spell["width"], sector_angle_deg=spell["angle"], bearing_deg=90,
            volume_m3=spell["maxVolume"], casting_seconds=spell["casting"], relaxation_seconds=60)
        if args.initial:
            still = spell["id"] in {"dos2-rain", "school-pool"}
            motion = "none" if still else "vortex" if spell["id"] in {
                "dq7-maelstrom", "rs3-maelstrom", "forspoken-cataract", "kaiju-titanosaurus-vortex"
            } else "directional" if spell["kind"] in {"rectangle", "sector"} else "radial"
            magic = WaterMagicConfig.model_validate({**magic.model_dump(), "release_mode":"initial",
                "initial_motion":motion, "initial_speed_mps":0 if still else 2,
                "vortex_core_radius_m":max(.5, spell["radius"]/2)})
        config = RunConfig(analysis_area=area, requested_accuracy_mode=AccuracyMode.FULL_1M, grid_cell_size_m=grid_m, water_magic=magic)
        source = resolve_rainfall(config)
        build = SfincsModelBuilder().build(root / name / "model", product, source)
        run = SfincsRunner().run(build.model_dir, logs_dir=root/name/"logs", engine=engine)
        with xr.open_dataset(run.result_path) as ds:
            times = ((ds.time.values-ds.time.values[0])/np.timedelta64(1,"s")).tolist()
            volumes = np.nansum(ds["h"].values, axis=(1,2)).astype(float) * grid_m**2
            end_cast = times.index(magic.casting_seconds)
            report = {"spell_id":spell["id"], "fixture":name, "kind":spell["kind"],
                "grid_cell_size_m":grid_m, "casting_seconds":magic.casting_seconds, "elapsed_seconds":times,
                "initial_volume_m3":float(volumes[0]), "release_mode":magic.release_mode,
                "requested_volume_m3":magic.volume_m3, "final_volume_m3":float(volumes[-1]),
                "relative_mass_error":abs(float(volumes[-1])/magic.volume_m3-1),
                "relaxation_volume_change_m3":float(volumes[-1]-volumes[end_cast]),
                "maximum_depth_m":float(np.nanmax(ds["h"].values)), "forcing":json.loads(source.source_metadata["source_report_json"])}
        if args.initial:
            assert abs(report["initial_volume_m3"]/magic.volume_m3-1) < .01
            assert not (build.model_dir/"water_magic.dis").exists()
        reports.append(report)
        print(json.dumps({k:v for k,v in report.items() if k not in {"forcing","elapsed_seconds"}}), flush=True)
    result = {"engine":str(engine.executable), "sha256":engine.sha256, "reports":reports}
    (root / "engine_validation.json").write_text(json.dumps(result,indent=2), encoding="utf-8")
    assert all(report["relative_mass_error"] < .01 for report in reports)
    assert all(abs(report["relaxation_volume_change_m3"]) < max(1e-6, report["requested_volume_m3"]*.001) for report in reports)
    assert all(report["maximum_depth_m"] > 0 for report in reports)


if __name__ == "__main__":
    main()
