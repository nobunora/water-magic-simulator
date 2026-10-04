"""HydroMT-SFINCS adapter for the Phase 3 Full 1 m model."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import xarray as xr
from pyproj import CRS

from floodsim.domain.rainfall import RainfallTimeSeries
from floodsim.domain.water_magic import MagicTimeSeries, magic_output_interval
from floodsim.preprocessing.adaptive_grid import AdaptiveGridProduct
from floodsim.preprocessing.full_grid import FullGridProduct
from floodsim.sfincs.adaptive_quadtree import create_adaptive_quadtree
from floodsim.sfincs.quadtree_writer import write_quadtree_grid_compat
from floodsim.sfincs.water_magic_forcing import MagicForcingError, write_magic_discharge
from floodsim.sfincs.water_magic_initial import write_magic_initial_state
from floodsim.storage.run_store import atomic_write_json

if TYPE_CHECKING:
    from floodsim.sfincs.variable_subgrid import SubgridPatchMetrics

EXPECTED_HYDROMT_SFINCS = "2.0.0rc3"


class ModelBuildError(RuntimeError):
    code = "MODEL_BUILD_FAILED"


@dataclass(frozen=True)
class ModelBuildResult:
    model_dir: Path
    report_path: Path
    report: dict[str, Any]
    adaptive_layout_path: Path | None = None


@contextmanager
def _sfincs_environment() -> Iterator[None]:
    """Hide host variables that collide with lazy rc3 settings loading."""
    debug_values = {
        key: value for key, value in os.environ.items() if key.casefold() == "debug"
    }
    for key in debug_values:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        os.environ.update(debug_values)


def _load_sfincs_model() -> Any:
    try:
        from hydromt_sfincs import SfincsModel  # type: ignore[import-untyped]
    except Exception as exc:  # pragma: no cover - environment dependent
        raise ModelBuildError("HydroMT-SFINCS 2.0.0rc3 is unavailable") from exc
    return SfincsModel


def _sfincs_datetime(value: datetime) -> datetime:
    """Return the timezone-naive UTC datetime expected by rc3 NetCDF writers."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _raster(values: np.ndarray, grid: FullGridProduct, name: str) -> xr.DataArray:
    x = grid.x0_m + grid.dx_m / 2.0 + grid.dx_m * np.arange(grid.width_cells, dtype=float)
    y = grid.y0_m + grid.dy_m / 2.0 + grid.dy_m * np.arange(grid.height_cells, dtype=float)
    data = xr.DataArray(values, dims=("y", "x"), coords={"x": x, "y": y}, name=name)
    data.raster.set_crs(CRS.from_wkt(grid.crs_wkt))
    data.raster.set_nodata(-9999)
    return data


def _precipitation(
    rainfall: RainfallTimeSeries,
    grid: FullGridProduct,
) -> xr.DataArray:
    if len(rainfall.elapsed_seconds) < 2:
        raise ModelBuildError("rainfall time series requires start and stop samples")
    start = _sfincs_datetime(rainfall.start_time)
    times = [
        start + timedelta(seconds=float(value)) for value in rainfall.elapsed_seconds
    ]
    rates = np.asarray(rainfall.intensity_mm_per_h, dtype=np.float32)
    values = rates[:, None, None] * grid.rain_weight[None, :, :]
    x = grid.x0_m + grid.dx_m / 2.0 + grid.dx_m * np.arange(grid.width_cells, dtype=float)
    y = grid.y0_m + grid.dy_m / 2.0 + grid.dy_m * np.arange(grid.height_cells, dtype=float)
    data = xr.DataArray(
        values,
        dims=("time", "y", "x"),
        coords={"time": times, "x": x, "y": y},
        name="precip_2d",
        attrs={"units": "mm/hr"},
    )
    data.raster.set_crs(CRS.from_wkt(grid.crs_wkt))
    data.raster.set_nodata(-9999)
    return data


def _configure_precipitation(
    model: Any,
    precip: xr.DataArray,
    rainfall: RainfallTimeSeries,
) -> None:
    """Set already-normalized gridded rainfall without re-entering DataCatalog."""
    component = getattr(model, "precipitation", None)
    if component is None or not hasattr(component, "set"):
        raise ModelBuildError("HydroMT-SFINCS precipitation component is incompatible")
    component.set(precip, name="precip_2d")
    model.config.set("netamprfile", "sfincs_netampr.nc")
    model.config.set("precipfile", None)
    interval = float(rainfall.elapsed_seconds[1] - rainfall.elapsed_seconds[0])
    if interval <= 0:
        raise ModelBuildError("rainfall forcing interval must be positive")
    model.config.set("dtwnd", min(1800.0, interval))


def _configure_neumann_boundary_compatibility(
    model: Any,
    root: Path,
    grid: FullGridProduct,
    duration_seconds: float,
) -> bool:
    """Provide the boundary time series required by the Windows engine for msk=6.

    The series is not applied to Neumann cells; SFINCS copies their adjacent
    regular-cell water level. It initializes the boundary update path that the
    released Windows executable otherwise skips and subsequently dereferences.
    """
    if not np.any(grid.sfincs_mask == 6):
        return False
    model.config.set("bndfile", "sfincs.bnd")
    model.config.set("bzsfile", "sfincs.bzs")
    center_x = grid.x0_m + grid.width_cells * grid.dx_m / 2.0
    center_y = grid.y0_m + grid.height_cells * grid.dy_m / 2.0
    (root / "sfincs.bnd").write_text(f"{center_x:.6f} {center_y:.6f}\n", encoding="utf-8")
    (root / "sfincs.bzs").write_text(
        f"0.0 0.0\n{duration_seconds:.6f} 0.0\n",
        encoding="utf-8",
    )
    return True


def derive_output_interval_seconds(duration_seconds: float) -> int:
    """Return a whole-minute output interval bounded by the v0.1 contract."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    raw = max(60.0, duration_seconds / 119.0)
    minutes = int(np.ceil(raw / 60.0))
    return min(900, max(60, minutes * 60))


class SfincsModelBuilder:
    """Build only the regular Full 1 m v0.1 hydraulic model."""

    def __init__(self, *, minimum_output_interval_seconds: int | None = None) -> None:
        if minimum_output_interval_seconds is not None and (
            minimum_output_interval_seconds < 60
            or minimum_output_interval_seconds % 60
        ):
            raise ValueError(
                "minimum_output_interval_seconds must be a positive whole minute"
            )
        self.minimum_output_interval_seconds = minimum_output_interval_seconds

    def _output_interval_seconds(self, duration_seconds: float) -> int:
        interval = derive_output_interval_seconds(duration_seconds)
        if self.minimum_output_interval_seconds is not None:
            interval = max(interval, self.minimum_output_interval_seconds)
        return min(int(duration_seconds), interval)

    def build(
        self,
        model_dir: str | Path,
        grid: FullGridProduct,
        rainfall: RainfallTimeSeries | MagicTimeSeries,
    ) -> ModelBuildResult:
        root = Path(model_dir)
        root.mkdir(parents=True, exist_ok=True)
        try:
            with _sfincs_environment():
                SfincsModel = _load_sfincs_model()
                model = SfincsModel(root=root, mode="w+", write_gis=False)
                # rc3 defers BaseSettings construction until config data is first
                # accessed, so initialize it while the generic host DEBUG is hidden.
                _ = model.config.data
            # rc3's regular-grid create() requires an integer EPSG during
            # initialization. The normalized providers deliberately use a local
            # AEQD CRS, which has no EPSG code. Initialize with a valid temporary
            # EPSG, then replace every rc3 CRS owner with the canonical AEQD CRS.
            # SfincsGrid.crs checks the instance `epsg` attribute before the
            # Dataset CRS, so clearing only config.epsg leaves the temporary 4326
            # active. Keep config, grid.epsg and crsgeo synchronized explicitly.
            model.grid.create(
                x0=grid.x0_m,
                y0=grid.y0_m,
                dx=grid.dx_m,
                dy=grid.dy_m,
                nmax=grid.height_cells,
                mmax=grid.width_cells,
                rotation=0,
                epsg=4326,
            )
            crs = CRS.from_wkt(grid.crs_wkt)
            model.grid.data.raster.set_crs(crs)
            model.grid.epsg = None
            model.config.set("epsg", None)
            model.config.set("crsgeo", int(crs.is_geographic))
            if not model.grid.crs.equals(crs):
                raise ModelBuildError(
                    "HydroMT-SFINCS did not retain the normalized model CRS"
                )

            elevation = _raster(grid.elevation_m, grid, "elevtn")
            roughness = _raster(grid.manning_n, grid, "manning")
            model.elevation.create([{"elevation": elevation}])
            model.mask.create()
            model.mask.data["mask"].values[:] = grid.sfincs_mask
            model.roughness.create([{"manning": roughness}])

            duration_seconds = float(rainfall.elapsed_seconds[-1])
            output_interval = (
                magic_output_interval(rainfall.config.casting_seconds, rainfall.config.relaxation_seconds)
                if isinstance(rainfall, MagicTimeSeries)
                else self._output_interval_seconds(duration_seconds)
            )
            start = _sfincs_datetime(rainfall.start_time)
            stop = start + timedelta(seconds=duration_seconds)
            stamp = "%Y%m%d %H%M%S"
            model.config.set("tref", start.strftime(stamp))
            model.config.set("tstart", start.strftime(stamp))
            model.config.set("tstop", stop.strftime(stamp))
            model.config.set("dtmapout", output_interval)
            model.config.set("dtmaxout", duration_seconds)
            model.config.set("dthisout", output_interval)
            model.config.set("outputformat", "net")
            model.config.set("coriolis", 0)
            model.config.set("alpha", 0.70)
            model.config.set("huthresh", 0.005)
            model.config.set("storecumprcp", 0)
            model.config.set("storevel", 1)

            if isinstance(rainfall, MagicTimeSeries):
                if rainfall.config.release_mode == "initial":
                    model.config.set("rstfile", "water_magic_initial.rst")
                else:
                    source_report = write_magic_discharge(root, rainfall, grid)
                    rainfall.source_metadata["source_report_json"] = json.dumps(source_report)
                    model.config.set("srcfile", "water_magic.src")
                    model.config.set("disfile", "water_magic.dis")
                # Maximum-volume narrow sources need finer steps to conserve
                # water during wetting; verified against 0.003 s convergence.
                model.config.set("dtmax", min(0.01, rainfall.config.transition_seconds))
                model.config.set("huthresh", 0.000001)
                # Start dry even when terrain elevations are below sea level.
                model.config.set("zsini", float(np.min(grid.elevation_m[grid.sfincs_mask > 0])) - 1.0)
            else:
                _configure_precipitation(model, _precipitation(rainfall, grid), rainfall)
            neumann_boundary_compatibility = _configure_neumann_boundary_compatibility(
                model,
                root,
                grid,
                duration_seconds,
            )
            model.write()
            if isinstance(rainfall, MagicTimeSeries) and rainfall.config.release_mode == "initial":
                source_report = write_magic_initial_state(root, rainfall, grid)
                rainfall.source_metadata["source_report_json"] = json.dumps(source_report)
        except (ModelBuildError, MagicForcingError):
            raise
        except Exception as exc:
            raise ModelBuildError(
                "failed to build Full 1 m HydroMT-SFINCS model"
            ) from exc

        report = {
            "schema_version": "1",
            "grid_type": "regular",
            "grid_resolution_m": grid.dx_m,
            "cell_counts": {f"{grid.dx_m:g}m": grid.cell_count},
            "model_crs_wkt": grid.crs_wkt,
            "active_cells": int(np.count_nonzero(grid.sfincs_mask)),
            "blocked_building_cells": int(np.count_nonzero(grid.building_mask)),
            "outflow_boundary_cells": int(np.count_nonzero(grid.sfincs_mask == 3)),
            "neumann_boundary_cells": int(np.count_nonzero(grid.sfincs_mask == 6)),
            "neumann_boundary_compatibility_boundary_file": neumann_boundary_compatibility,
            "roughness": {"general": 0.030, "road": 0.030},
            "rainfall_volume_before_weight_area_m2": grid.roof_allocation.meteorological_area_m2,
            "rainfall_volume_after_weight_area_m2": grid.roof_allocation.hydraulic_weighted_area_m2,
            "roof_rain_relative_mass_error": grid.roof_allocation.relative_mass_error,
            "output_interval_seconds": output_interval,
            "maximum_output_interval_seconds": duration_seconds,
            "cumulative_precipitation_output": False,
            "velocity_output": {"storevel": 1, "variables": ["u", "v"]},
            "numerics": {"alpha": 0.70, "huthresh_m": 0.005},
            "unsupported_physics": {
                "infiltration": False,
                "sewer_drainage": False,
                "water_level_boundary": False,
                "tide": False,
                "wind_waves": False,
                "river_inflow": False,
                "building_interior_storage": False,
            },
            "warnings": [],
        }
        if isinstance(rainfall, MagicTimeSeries):
            report["water_magic"] = json.loads(rainfall.source_metadata["source_report_json"])
            report["numerics"] = {"alpha": 0.70, "huthresh_m": 0.000001, "initial_state": "wet_restart" if rainfall.config.release_mode == "initial" else "dry",
                "dtmax_seconds": min(0.01, rainfall.config.transition_seconds)}
            forcing_files = ["water_magic_initial.rst"] if rainfall.config.release_mode == "initial" else ["water_magic.src", "water_magic.dis"]
            forcing_hash = hashlib.sha256(b"".join((root / name).read_bytes() for name in forcing_files)).hexdigest()
            report["forcing_sha256"] = forcing_hash
            rainfall.source_metadata["forcing_sha256"] = forcing_hash
        report_path = root / "model_build_report.json"
        atomic_write_json(report_path, report)
        return ModelBuildResult(root, report_path, report)


ADAPTIVE_SUBGRID_PIXELS = 8
ADAPTIVE_SUBGRID_LEVELS = 10
ADAPTIVE_SUBGRID_STRATEGIES = {"uniform", "2-2-4-8", "2-2-4-8-optimized"}


class AdaptiveSfincsModelBuilder:
    """Build Adaptive SFINCS runs from a rainfall-independent static bundle."""

    _STATIC_FILES = (
        "sfincs.nc",
        "sfincs_subgrid.nc",
        "adaptive_face_layout.npz",
        "static_model.json",
    )

    def __init__(
        self,
        *,
        subgrid_pixels: int = ADAPTIVE_SUBGRID_PIXELS,
        subgrid_levels: int = ADAPTIVE_SUBGRID_LEVELS,
        subgrid_strategy: str = "2-2-4-8-optimized",
        cache_root: str | Path | None = None,
    ) -> None:
        if subgrid_pixels < 1:
            raise ValueError("subgrid_pixels must be positive")
        if subgrid_levels < 2:
            raise ValueError("subgrid_levels must be at least two")
        if subgrid_strategy not in ADAPTIVE_SUBGRID_STRATEGIES:
            raise ValueError(f"unsupported subgrid_strategy: {subgrid_strategy}")
        self.subgrid_pixels = int(subgrid_pixels)
        self.subgrid_levels = int(subgrid_levels)
        self.subgrid_strategy = subgrid_strategy
        self.cache_root = Path(cache_root) if cache_root is not None else None

    def _subgrid_cache_key(
        self,
        grid: FullGridProduct,
        adaptive: AdaptiveGridProduct,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(b"adaptive-static-model-v4-source-1m")
        digest.update(str(self.subgrid_pixels).encode())
        digest.update(str(self.subgrid_levels).encode())
        digest.update(self.subgrid_strategy.encode())
        if self.subgrid_strategy == "2-2-4-8-optimized":
            digest.update(b"direct-parallel-fine-kernels-v2")
        digest.update(grid.crs_wkt.encode("utf-8"))
        for value in (
            grid.width_cells,
            grid.height_cells,
            grid.dx_m,
            grid.dy_m,
            grid.x0_m,
            grid.y0_m,
        ):
            digest.update(repr(value).encode("ascii"))
            digest.update(b"\\0")
        for values in (
            grid.elevation_m,
            grid.manning_n,
            grid.sfincs_mask,
            adaptive.resolution_m,
        ):
            array = np.ascontiguousarray(values)
            digest.update(str(array.shape).encode())
            digest.update(array.dtype.str.encode())
            digest.update(memoryview(array).cast("B"))
        return digest.hexdigest()[:32]

    def _cache_dir(self, key: str) -> Path | None:
        return None if self.cache_root is None else self.cache_root / key

    def _validate_static_bundle(
        self,
        bundle: Path,
        *,
        key: str,
        grid: FullGridProduct,
    ) -> dict[str, Any] | None:
        if not all((bundle / name).is_file() for name in self._STATIC_FILES):
            return None
        try:
            metadata = json.loads((bundle / "static_model.json").read_text(encoding="utf-8"))
            if metadata.get("schema") != "adaptive-static-model-v4-source-1m":
                return None
            if metadata.get("cache_key") != key:
                return None
            with xr.open_dataset(bundle / "sfincs_subgrid.nc") as dataset:
                if not dataset.data_vars:
                    return None
            with xr.open_dataset(bundle / "sfincs.nc") as dataset:
                if not dataset.variables:
                    return None
            with np.load(bundle / "adaptive_face_layout.npz", allow_pickle=False) as layout:
                required = {
                    "resolution_m",
                    "row_index",
                    "col_index",
                    "source_overlap_area_m2",
                    "sfincs_mask",
                    "source_height_cells",
                    "source_width_cells",
                }
                if not required.issubset(layout.files):
                    return None
                if int(layout["source_height_cells"]) != grid.height_cells:
                    return None
                if int(layout["source_width_cells"]) != grid.width_cells:
                    return None
                face_count = len(layout["resolution_m"])
                if any(len(layout[name]) != face_count for name in (
                    "row_index", "col_index", "source_overlap_area_m2", "sfincs_mask"
                )):
                    return None
            return metadata
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _materialize_static_bundle(self, bundle: Path, root: Path) -> None:
        for name in ("sfincs.nc", "sfincs_subgrid.nc", "adaptive_face_layout.npz"):
            shutil.copy2(bundle / name, root / name)

    def _publish_static_bundle(
        self,
        cache_dir: Path,
        *,
        root: Path,
        metadata: dict[str, Any],
    ) -> None:
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{cache_dir.name}.", dir=cache_dir.parent)
        )
        try:
            for name in ("sfincs.nc", "sfincs_subgrid.nc", "adaptive_face_layout.npz"):
                shutil.copy2(root / name, staging / name)
            atomic_write_json(staging / "static_model.json", metadata)
            try:
                staging.rename(cache_dir)
            except FileExistsError:
                # Another identical build won the publish race. Its complete
                # atomically-published entry is authoritative.
                pass
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def build(
        self,
        model_dir: str | Path,
        grid: FullGridProduct,
        adaptive: AdaptiveGridProduct,
        rainfall: RainfallTimeSeries,
    ) -> ModelBuildResult:
        root = Path(model_dir)
        root.mkdir(parents=True, exist_ok=True)
        cache_key = self._subgrid_cache_key(grid, adaptive)
        cache_dir = self._cache_dir(cache_key)
        static_metadata: dict[str, Any] | None = None
        static_cache_hit = False
        layout_path = root / "adaptive_face_layout.npz"
        build_started = time.perf_counter()
        phase_started = build_started
        phase_timings: dict[str, float] = {}

        try:
            if cache_dir is not None:
                static_metadata = self._validate_static_bundle(
                    cache_dir, key=cache_key, grid=grid
                )
                if static_metadata is not None:
                    self._materialize_static_bundle(cache_dir, root)
                    static_cache_hit = True
                elif cache_dir.exists():
                    # Published entries are immutable. If validation fails,
                    # discard the whole entry rather than mixing old/new files.
                    shutil.rmtree(cache_dir, ignore_errors=True)
            phase_timings["cache_lookup_materialize_s"] = time.perf_counter() - phase_started
            phase_started = time.perf_counter()

            with _sfincs_environment():
                SfincsModel = _load_sfincs_model()
                model = SfincsModel(root=root, mode="w+", write_gis=False)
                _ = model.config.data
            phase_timings["hydromt_model_init_s"] = time.perf_counter() - phase_started
            phase_started = time.perf_counter()

            if static_cache_hit:
                # Static topology/subgrid files are already complete. Do not
                # reconstruct HydroMT quadtree/subgrid objects on rainfall-only
                # runs; only point the dynamic config at the materialized files.
                model.config.set("qtrfile", "sfincs.nc")
                model.config.set("sbgfile", "sfincs_subgrid.nc")
                phase_timings["static_cache_hit_setup_s"] = time.perf_counter() - phase_started
                phase_started = time.perf_counter()
            else:
                quadtree = create_adaptive_quadtree(model, grid, adaptive)
                phase_timings["quadtree_create_s"] = time.perf_counter() - phase_started
                phase_started = time.perf_counter()
                elevation = _raster(grid.elevation_m, grid, "elevtn")
                roughness = _raster(grid.manning_n, grid, "manning")
                component = getattr(model, "quadtree_subgrid", None)
                if component is None or not hasattr(component, "create"):
                    raise ModelBuildError(
                        "HydroMT-SFINCS quadtree subgrid component is incompatible"
                    )
                subgrid_cpu_started = time.process_time()
                variable_patch: SubgridPatchMetrics | None = None
                if self.subgrid_strategy == "2-2-4-8-optimized":
                    with _sfincs_environment():
                        from floodsim.sfincs.variable_subgrid import (
                            build_optimized_2248_subgrid,
                        )

                        component._data, variable_patch = build_optimized_2248_subgrid(
                            model.quadtree_grid.data,
                            grid.elevation_m,
                            grid.manning_n,
                            nr_levels=self.subgrid_levels,
                        )
                    base_subgrid_pixels = 0
                else:
                    base_subgrid_pixels = (
                        2
                        if self.subgrid_strategy == "2-2-4-8"
                        else self.subgrid_pixels
                    )
                    component.create(
                        elevation_list=[{"elevation": elevation}],
                        roughness_list=[{"manning": roughness}],
                        nr_levels=self.subgrid_levels,
                        nr_subgrid_pixels=base_subgrid_pixels,
                        write_dep_tif=False,
                        write_man_tif=False,
                        quiet=True,
                    )
                    if self.subgrid_strategy == "2-2-4-8":
                        with _sfincs_environment():
                            from floodsim.sfincs.variable_subgrid import (
                                apply_2248_subgrid_patch,
                            )

                            variable_patch = apply_2248_subgrid_patch(
                                component.data,
                                model.quadtree_grid.data,
                                grid.elevation_m,
                                grid.manning_n,
                                nr_levels=self.subgrid_levels,
                            )
                phase_timings["subgrid_create_s"] = time.perf_counter() - phase_started
                phase_timings["subgrid_create_cpu_s"] = (
                    time.process_time() - subgrid_cpu_started
                )
                phase_started = time.perf_counter()
                if not getattr(component.data, "data_vars", None):
                    raise ModelBuildError("Adaptive subgrid generation produced no data")

                subgrid_face_count = int(component.data["z_zmin"].size)
                subgrid_uv_point_count = int(component.data["uv_zmin"].size)
                subgrid_sample_evaluations = (
                    subgrid_face_count + subgrid_uv_point_count
                ) * base_subgrid_pixels * base_subgrid_pixels
                if self.subgrid_strategy == "2-2-4-8-optimized":
                    if variable_patch is None:
                        raise ModelBuildError(
                            "Optimized Adaptive subgrid did not report build metrics"
                        )
                    subgrid_sample_evaluations = variable_patch[
                        "patch_sample_evaluations"
                    ]
                elif variable_patch is not None:
                    subgrid_sample_evaluations += variable_patch[
                        "patch_sample_evaluations"
                    ]

                write_quadtree_grid_compat(model.quadtree_grid, filename="sfincs.nc")
                component.write(filename="sfincs_subgrid.nc")

                face_rows = (
                    np.asarray(model.quadtree_grid.data["n"].values, dtype=np.int32) - 1
                )
                face_cols = (
                    np.asarray(model.quadtree_grid.data["m"].values, dtype=np.int32) - 1
                )
                np.savez_compressed(
                    layout_path,
                    resolution_m=quadtree.face_fields.resolution_m.astype(
                        np.int16, copy=False
                    ),
                    row_index=face_rows,
                    col_index=face_cols,
                    source_overlap_area_m2=quadtree.face_fields.source_overlap_area_m2,
                    sfincs_mask=quadtree.face_fields.sfincs_mask.astype(
                        np.uint8, copy=False
                    ),
                    source_height_cells=np.int32(grid.height_cells),
                    source_width_cells=np.int32(grid.width_cells),
                )
                with xr.open_dataset(root / "sfincs_subgrid.nc") as dataset:
                    subgrid_variables = sorted(str(name) for name in dataset.data_vars)
                level_counts = {
                    f"{size}m": int(
                        np.count_nonzero(quadtree.face_fields.resolution_m == size)
                    )
                    for size in (1, 2, 4, 8, 16, 32)
                    if np.any(quadtree.face_fields.resolution_m == size)
                }
                static_metadata = {
                    "schema": "adaptive-static-model-v4-source-1m",
                    "cache_key": cache_key,
                    "cell_counts": level_counts,
                    "total_hydraulic_cells": quadtree.face_count,
                    "quadtree_padded_width_m": quadtree.padded_width_m,
                    "quadtree_padded_height_m": quadtree.padded_height_m,
                    "active_cells": int(
                        np.count_nonzero(quadtree.face_fields.sfincs_mask)
                    ),
                    "outflow_boundary_cells": int(
                        np.count_nonzero(quadtree.face_fields.sfincs_mask == 3)
                    ),
                    "hydraulic_weighted_area_m2": (
                        quadtree.face_fields.hydraulic_weighted_area_m2
                    ),
                    "subgrid_variables": subgrid_variables,
                    "subgrid_face_count": subgrid_face_count,
                    "subgrid_uv_point_count": subgrid_uv_point_count,
                    "subgrid_sample_evaluations": subgrid_sample_evaluations,
                    "subgrid_strategy": self.subgrid_strategy,
                    "subgrid_variable_patch": variable_patch,
                }
                phase_timings["static_write_layout_s"] = time.perf_counter() - phase_started
                phase_started = time.perf_counter()
                if cache_dir is not None:
                    self._publish_static_bundle(
                        cache_dir, root=root, metadata=static_metadata
                    )
                phase_timings["static_publish_s"] = time.perf_counter() - phase_started
                phase_started = time.perf_counter()

            if static_metadata is None:
                raise ModelBuildError("Adaptive static model metadata is unavailable")

            duration_seconds = float(rainfall.elapsed_seconds[-1])
            output_interval = derive_output_interval_seconds(duration_seconds)
            start = _sfincs_datetime(rainfall.start_time)
            stop = start + timedelta(seconds=duration_seconds)
            stamp = "%Y%m%d %H%M%S"
            model.config.set("tref", start.strftime(stamp))
            model.config.set("tstart", start.strftime(stamp))
            model.config.set("tstop", stop.strftime(stamp))
            model.config.set("dtmapout", output_interval)
            model.config.set("dtmaxout", duration_seconds)
            model.config.set("dthisout", output_interval)
            model.config.set("outputformat", "net")
            model.config.set("coriolis", 0)
            model.config.set("alpha", 0.75)
            model.config.set("storecumprcp", 0)
            model.config.set("storevel", 1)
            model.config.set("storehsubgrid", 1)
            model.config.set("storezvolume", 1)
            model.config.set("regular_output_on_mesh", 1)
            _configure_precipitation(model, _precipitation(rainfall, grid), rainfall)
            model.precipitation.write(filename="sfincs_netampr.nc")
            model.config.write()
            phase_timings["dynamic_rainfall_config_s"] = time.perf_counter() - phase_started
            phase_timings["total_build_s"] = time.perf_counter() - build_started
        except ModelBuildError:
            raise
        except Exception as exc:
            raise ModelBuildError(
                "failed to build Adaptive HydroMT-SFINCS quadtree/subgrid model"
            ) from exc

        total_hydraulic_cells = int(static_metadata["total_hydraulic_cells"])
        report = {
            "schema_version": "1",
            "grid_type": "quadtree",
            "cell_counts": dict(static_metadata["cell_counts"]),
            "total_hydraulic_cells": total_hydraulic_cells,
            "full_1m_equivalent_cells": adaptive.full_1m_equivalent_cells,
            "reduction_ratio": total_hydraulic_cells / adaptive.full_1m_equivalent_cells,
            "classifier_reduction_ratio": adaptive.reduction_ratio,
            "threshold_identity": adaptive.threshold_identity,
            "classifier_diagnostics": dict(adaptive.diagnostics),
            "model_crs_wkt": grid.crs_wkt,
            "quadtree_base_resolution_m": 32.0,
            "quadtree_padded_width_m": static_metadata["quadtree_padded_width_m"],
            "quadtree_padded_height_m": static_metadata["quadtree_padded_height_m"],
            "active_cells": int(static_metadata["active_cells"]),
            "outflow_boundary_cells": int(static_metadata["outflow_boundary_cells"]),
            "blocked_building_source_cells": int(np.count_nonzero(grid.building_mask)),
            "roughness": {"general": 0.030, "road": 0.030},
            "build_phase_timings_seconds": phase_timings,
            "static_model_cache": {
                "cache_hit": static_cache_hit,
                "cache_key": cache_key,
                "artifacts": [
                    "sfincs.nc",
                    "sfincs_subgrid.nc",
                    "adaptive_face_layout.npz",
                ],
                "rainfall_independent": True,
            },
            "subgrid": {
                "cache_hit": static_cache_hit,
                "source_terrain_resolution_m": 1.0,
                "source_roughness_resolution_m": 1.0,
                "pixels_per_hydraulic_cell": self.subgrid_pixels,
                "strategy": self.subgrid_strategy,
                "pixels_by_cell_size_m": (
                    {"1": 2, "2": 2, "4": 4, "8": 8}
                    if self.subgrid_strategy
                    in {"2-2-4-8", "2-2-4-8-optimized"}
                    else None
                ),
                "effective_coarsest_subpixel_m": (
                    1.0
                    if self.subgrid_strategy
                    in {"2-2-4-8", "2-2-4-8-optimized"}
                    else max(adaptive.active_levels_m) / self.subgrid_pixels
                ),
                "optimizations": (
                    [
                        "elide_repeated_1m_2x2_samples",
                        "direct_aligned_2m_4m_8m_tables",
                        "selected_uv_ordered_scan",
                        "no_global_uv_sort",
                        "parallel_face_uv_tables",
                        "specialized_1m_2m_kernels",
                    ]
                    if self.subgrid_strategy == "2-2-4-8-optimized"
                    else []
                ),
                "hypsometric_levels": self.subgrid_levels,
                "variables": list(static_metadata["subgrid_variables"]),
                "face_count": static_metadata.get("subgrid_face_count"),
                "uv_point_count": static_metadata.get("subgrid_uv_point_count"),
                "sample_evaluations": static_metadata.get(
                    "subgrid_sample_evaluations"
                ),
            },
            "rainfall_forcing": {
                "grid_resolution_m": 1.0,
                "spatial_mode": "uniform_meteorology_with_roof_allocation_weights",
                "weighted_area_m2": float(
                    static_metadata["hydraulic_weighted_area_m2"]
                ),
            },
            "rainfall_volume_before_weight_area_m2": (
                grid.roof_allocation.meteorological_area_m2
            ),
            "rainfall_volume_after_weight_area_m2": (
                grid.roof_allocation.hydraulic_weighted_area_m2
            ),
            "roof_rain_relative_mass_error": grid.roof_allocation.relative_mass_error,
            "output_interval_seconds": output_interval,
            "maximum_output_interval_seconds": duration_seconds,
            "cumulative_precipitation_output": False,
            "depth_output": {"storehsubgrid": 1, "variables": ["h", "hmax"]},
            "volume_output": {"storezvolume": 1, "variable": "subgrid_volume"},
            "velocity_output": {"storevel": 1, "variables": ["u", "v"]},
            "mesh_output": {
                "regular_output_on_mesh": 1,
                "face_dimension": "nmesh2d_face",
            },
            "adaptive_face_layout": "adaptive_face_layout.npz",
            "unsupported_physics": {
                "infiltration": False,
                "sewer_drainage": False,
                "water_level_boundary": False,
                "tide": False,
                "wind_waves": False,
                "river_inflow": False,
                "building_interior_storage": False,
            },
            "warnings": [],
        }
        report_path = root / "model_build_report.json"
        atomic_write_json(report_path, report)
        return ModelBuildResult(root, report_path, report, layout_path)

