"""Persist SFINCS regular-grid output behind an unambiguous result contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from floodsim.domain.geometry import AnalysisArea
from floodsim.domain.manifest import Limitations
from floodsim.results.regular_netcdf_source import (
    RegularNetcdfSource,
    scan_regular_diagnostics,
)
from floodsim.results.regular_queries import prepare_regular_queries
from floodsim.results.view import (
    AdaptiveNormalizedArrays,
    NormalizedArrays,
    depth_display_range,
    depth_legend_metadata,
    elevation_legend_metadata,
)
from floodsim.sfincs.output_reader import SfincsQuadtreeResult, SfincsRegularResult
from floodsim.storage.run_store import atomic_write_json


@dataclass(frozen=True)
class NormalizedResult:
    arrays_path: Path
    metadata_path: Path
    metadata: dict[str, Any]


@dataclass(frozen=True)
class NetcdfFinalizedResult:
    """Regular result artefacts that retain NetCDF instead of a dense NPZ."""

    descriptor_path: Path
    metadata_path: Path
    metadata: dict[str, Any]


def finalize_regular_netcdf_result(
    source: RegularNetcdfSource,
    *,
    model_dir: str | Path,
    results_dir: str | Path,
    limitations: Limitations,
    provider_summary: Mapping[str, Any] | None = None,
    engine_summary: Mapping[str, Any] | None = None,
    run_summary: Mapping[str, Any] | None = None,
) -> NetcdfFinalizedResult:
    """Persist a descriptor and bounded diagnostics for a regular NetCDF source."""
    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    diagnostics = scan_regular_diagnostics(source, model_dir=model_dir)
    prepare_regular_queries(source, model_dir=model_dir)
    metadata = {
        "schema_version": "1",
        "storage_kind": "regular_netcdf_source",
        "bounds": source.bounds,
        "units": {"water_depth": "m", "terrain_elevation": "m", "grid_resolution": "m"},
        "available_time_indices": list(range(len(source.time_values))),
        "time_values": list(source.time_values),
        "flow_vectors_available": source.flow_vectors_available,
        "chunk_shape": list(source.chunk_shape),
        "cache_schema_revision": 1,
        "max_depth_summary": diagnostics,
        "grid_level_summary": {f"{source.block_size_m:g}m": diagnostics["active_cells"]},
        "depth_legend": depth_legend_metadata(
            min(diagnostics["min_visible_depth_m"], diagnostics["global_max_depth_m"]), diagnostics["global_max_depth_m"]
        ),
        "elevation_legend": elevation_legend_metadata(
            diagnostics["terrain_min_elevation_m"],
            diagnostics["terrain_max_elevation_m"],
        ),
        "provider_summary": dict(provider_summary or {}),
        "engine_summary": dict(engine_summary or {}),
        "run_summary": dict(run_summary or {}),
        "no_data_policy": "Regular SFINCS NetCDF is retained; viewport reads are bounded to source chunks.",
        "limitations": limitations.model_dump(),
    }
    descriptor_path = root / "regular_netcdf_source.json"
    metadata_path = root / "result_metadata.json"
    atomic_write_json(descriptor_path, source.to_json())
    atomic_write_json(metadata_path, metadata)
    # A successful rename is not sufficient proof when a process may be interrupted.
    if not descriptor_path.is_file() or not metadata_path.is_file():
        raise RuntimeError("regular result finalizer did not persist its artefacts")
    return NetcdfFinalizedResult(descriptor_path, metadata_path, metadata)


def normalize_regular_result(
    result: SfincsRegularResult,
    *,
    area: AnalysisArea,
    results_dir: str | Path,
    limitations: Limitations,
    provider_summary: Mapping[str, Any] | None = None,
    engine_summary: Mapping[str, Any] | None = None,
    run_summary: Mapping[str, Any] | None = None,
    grid_resolution_m: float = 1.0,
) -> NormalizedResult:
    if grid_resolution_m <= 0:
        raise ValueError("grid_resolution_m must be positive")
    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    arrays_path = root / "normalized_full_1m.npz"
    arrays_payload: dict[str, Any] = {
        "depth_time_m": result.depth_time_m,
        "max_depth_m": result.max_depth_m,
        "terrain_elevation_m": result.terrain_elevation_m,
        "active_mask": result.active_mask,
        "time_values": np.asarray(result.time_values),
        "grid_resolution_m": np.float32(grid_resolution_m),
    }
    if result.flow_vectors_available:
        arrays_payload["velocity_u_mps"] = result.velocity_u_mps
        arrays_payload["velocity_v_mps"] = result.velocity_v_mps
        arrays_payload["velocity_grid_stride"] = np.int32(result.velocity_grid_stride)
    # Keep the normal run path fast. Portable compression is applied only when
    # the user explicitly exports a result archive.
    np.savez(arrays_path, **arrays_payload)
    metadata = {
        "schema_version": "1",
        "bounds": area.bounds.model_dump(),
        "units": {
            "water_depth": "m",
            "terrain_elevation": "m",
            "grid_resolution": "m",
        },
        "available_time_indices": list(range(len(result.time_values))),
        "time_values": list(result.time_values),
        "flow_vectors_available": result.flow_vectors_available,
        "max_depth_summary": {
            "global_max_depth_m": result.global_max_depth_m,
            "hmax_reconstructed_cells": result.hmax_reconstructed_cells,
            "negative_depth_clipped_values": result.negative_depth_clipped_values,
            "negative_max_depth_clipped_cells": result.negative_max_depth_clipped_cells,
            "min_raw_active_depth_m": result.min_raw_active_depth_m,
            "excluded_boundary_cells": result.excluded_boundary_cells,
        },
        "grid_level_summary": {
            f"{grid_resolution_m:g}m": int(np.count_nonzero(result.active_mask)),
        },
        "depth_legend": depth_legend_metadata(*depth_display_range(NormalizedArrays(
            depth_time_m=result.depth_time_m,
            max_depth_m=result.max_depth_m,
            terrain_elevation_m=result.terrain_elevation_m,
            active_mask=result.active_mask,
            time_values=result.time_values,
            grid_resolution_m=grid_resolution_m,
        ))),
        "elevation_legend": elevation_legend_metadata(
            float(np.min(result.terrain_elevation_m[result.active_mask])),
            float(np.max(result.terrain_elevation_m[result.active_mask])),
        ),
        "provider_summary": dict(provider_summary or {}),
        "engine_summary": dict(engine_summary or {}),
        "run_summary": dict(run_summary or {}),
        "no_data_policy": (
            "inactive/blocked and SFINCS boundary-control cells are NaN in normalized arrays; "
            "regular-grid SFINCS h is an unfiltered signed zs-zb output, so finite negative "
            "time-depth values represent dry-state output and are normalized to zero while "
            "remaining visible in diagnostics; missing wet-filtered hmax is reconstructed from "
            "the normalized time-depth series; finite negative hmax remains invalid"
        ),
        "limitations": limitations.model_dump(),
    }
    metadata_path = root / "result_metadata.json"
    atomic_write_json(metadata_path, metadata)
    return NormalizedResult(arrays_path, metadata_path, metadata)



def normalize_quadtree_result(
    result: SfincsQuadtreeResult,
    *,
    area: AnalysisArea,
    results_dir: str | Path,
    limitations: Limitations,
    provider_summary: Mapping[str, Any] | None = None,
    engine_summary: Mapping[str, Any] | None = None,
    run_summary: Mapping[str, Any] | None = None,
) -> NormalizedResult:
    """Persist Adaptive output in its native quadtree-face representation.

    The hydraulic result stays in time-by-face form. Rendering and point
    inspection may project individual faces to display/source-grid space later,
    but the normalized artifact never expands every time frame back to Full 1 m.
    """

    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    arrays_path = root / "normalized_adaptive_faces.npz"
    layout = result.layout
    arrays_payload: dict[str, Any] = {
        "storage_kind": np.asarray("quadtree_faces"),
        "depth_time_m": result.depth_time_m,
        "max_depth_m": result.max_depth_m,
        "terrain_elevation_m": result.terrain_elevation_m,
        "active_mask": result.active_mask,
        "time_values": np.asarray(result.time_values),
        "face_resolution_m": layout.resolution_m,
        "face_row_index": layout.row_index,
        "face_col_index": layout.col_index,
        "face_source_overlap_area_m2": layout.source_overlap_area_m2,
        "source_height_cells": np.int32(layout.source_height_cells),
        "source_width_cells": np.int32(layout.source_width_cells),
    }
    if result.flow_vectors_available:
        arrays_payload["velocity_u_mps"] = result.velocity_u_mps
        arrays_payload["velocity_v_mps"] = result.velocity_v_mps
    if result.subgrid_volume_m3 is not None:
        arrays_payload["subgrid_volume_m3"] = result.subgrid_volume_m3
    # Keep the normal run path fast. Portable compression is applied only when
    # the user explicitly exports a result archive.
    np.savez(arrays_path, **arrays_payload)

    level_summary = {
        f"{level}m": int(
            np.count_nonzero(result.active_mask & (layout.resolution_m == level))
        )
        for level in (1, 2, 4, 8, 16, 32)
        if np.any(result.active_mask & (layout.resolution_m == level))
    }
    metadata = {
        "schema_version": "1",
        "bounds": area.bounds.model_dump(),
        "units": {
            "water_depth": "m",
            "terrain_elevation": "m",
            "grid_resolution": "m",
        },
        "available_time_indices": list(range(len(result.time_values))),
        "time_values": list(result.time_values),
        "flow_vectors_available": result.flow_vectors_available,
        "max_depth_summary": {
            "global_max_depth_m": result.global_max_depth_m,
            "hmax_reconstructed_cells": result.hmax_reconstructed_cells,
            "dry_fill_depth_values": result.dry_fill_depth_values,
            "negative_depth_clipped_values": result.negative_depth_clipped_values,
            "negative_max_depth_clipped_cells": 0,
            "min_raw_active_depth_m": result.min_raw_active_depth_m,
            "excluded_boundary_cells": result.excluded_boundary_cells,
        },
        "grid_level_summary": level_summary,
        "depth_legend": depth_legend_metadata(*depth_display_range(AdaptiveNormalizedArrays(
            depth_time_m=result.depth_time_m,
            max_depth_m=result.max_depth_m,
            terrain_elevation_m=result.terrain_elevation_m,
            active_mask=result.active_mask,
            time_values=result.time_values,
            face_resolution_m=layout.resolution_m,
            face_row_index=layout.row_index,
            face_col_index=layout.col_index,
            face_source_overlap_area_m2=layout.source_overlap_area_m2,
            source_height_cells=layout.source_height_cells,
            source_width_cells=layout.source_width_cells,
        ))),
        "elevation_legend": elevation_legend_metadata(
            float(np.min(result.terrain_elevation_m[result.active_mask])),
            float(np.max(result.terrain_elevation_m[result.active_mask])),
        ),
        "provider_summary": dict(provider_summary or {}),
        "engine_summary": dict(engine_summary or {}),
        "run_summary": dict(run_summary or {}),
        "no_data_policy": (
            "Adaptive output is stored in native quadtree-face order; inactive/blocked "
            "and SFINCS boundary-control faces are NaN; SFINCS wet-filtered quadtree h "
            "FILL_VALUE samples on dry active faces are normalized to zero and counted "
            "in diagnostics; finite negative time-depth values are also normalized to "
            "zero while remaining visible in diagnostics; missing wet-filtered hmax is "
            "reconstructed from normalized time-depth; infinite h and finite negative "
            "hmax remain invalid"
        ),
        "limitations": limitations.model_dump(),
    }
    metadata_path = root / "result_metadata.json"
    atomic_write_json(metadata_path, metadata)
    return NormalizedResult(arrays_path, metadata_path, metadata)
