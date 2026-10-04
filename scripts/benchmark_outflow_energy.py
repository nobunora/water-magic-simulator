"""Measure frame-by-frame estimated outflow energy on existing regular results."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
import xarray as xr

from floodsim.results.outflow_energy import accumulate_interval, outflow_rates
from floodsim.results.regular_netcdf_source import (
    load_regular_netcdf_descriptor,
    validate_source_identity,
)


def benchmark(run_dir: Path, output: Path, *, storage="disk"):
    if storage not in ("disk", "memory"):
        raise ValueError("storage must be disk or memory")
    source = load_regular_netcdf_descriptor(run_dir / "results/regular_netcdf_source.json")
    source_path = validate_source_identity(source, model_dir=run_dir / "model")
    output.mkdir(parents=True)
    timings = {"read_seconds": 0., "calculation_seconds": 0., "save_seconds": 0., "final_aggregation_seconds": 0.}
    started = perf_counter()
    previous_q = previous_p = previous_time = None
    totals = work = None
    with xr.open_dataset(source_path) as dataset:
        active = dataset["msk"].values > 0
        for index in range(len(source.time_values)):
            stage = perf_counter()
            h, u, v = (np.asarray(dataset[key].isel(time=index).values, dtype=np.float64)
                       for key in ("h", "u", "v"))
            elapsed = float((dataset.time.values[index] - dataset.time.values[0]) / np.timedelta64(1, "s"))
            timings["read_seconds"] += perf_counter() - stage
            stage = perf_counter()
            q, p = outflow_rates(h, u, v, active, source.block_size_m)
            if storage == "memory":
                if totals is None:
                    totals = np.zeros((2, *q.shape), dtype=np.float64)
                    work = np.zeros((4, *q.shape), dtype=np.float64)
                    np.copyto(work[0], q)
                    np.copyto(work[1], p)
                else:
                    assert previous_time is not None
                    accumulate_interval(totals, work, q, p, elapsed - previous_time)
            elif previous_q is None:
                delta_volume, delta_energy = np.zeros_like(q), np.zeros_like(p)
            else:
                assert previous_time is not None
                dt = elapsed - previous_time
                delta_volume = (previous_q + q) * (.5 * dt)
                delta_energy = (previous_p + p) * (.5 * dt)
            timings["calculation_seconds"] += perf_counter() - stage
            if storage == "disk":
                stage = perf_counter()
                with (output / f"frame-{index:04}.npz").open("wb") as stream:
                    np.savez(stream, volume_m3=delta_volume, energy_j=delta_energy,
                             time_index=index, elapsed_seconds=elapsed)
                    stream.flush()
                    os.fsync(stream.fileno())
                timings["save_seconds"] += perf_counter() - stage
                previous_q, previous_p = q, p
            previous_time = elapsed
    stage = perf_counter()
    if storage == "memory":
        assert totals is not None
        total_volume, total_energy = totals
    else:
        total_volume, total_energy = np.zeros_like(previous_q), np.zeros_like(previous_p)
        for index in range(len(source.time_values)):
            with np.load(output / f"frame-{index:04}.npz", allow_pickle=False) as frame:
                total_volume += frame["volume_m3"]
                total_energy += frame["energy_j"]
    np.savez(output / "totals.npz", volume_m3=total_volume, energy_j=total_energy)
    positive = np.flatnonzero(total_energy.ravel() > 0)
    order = np.lexsort((positive, -total_energy.ravel()[positive]))[:10]
    rankings = []
    for rank, cell in enumerate(positive[order], 1):
        row, col = np.unravel_index(cell, total_energy.shape)
        energy = float(total_energy[row, col])
        rankings.append({"rank": rank, "row": int(row), "column": int(col), "total_energy_j": energy,
                         "total_outflow_m3": float(total_volume[row, col]),
                         "prius_equivalent_kmh": float(3.6 * np.sqrt(2 * energy / 1400))})
    (output / "ranking.json").write_text(json.dumps(rankings, indent=2), encoding="utf-8")
    timings["final_aggregation_seconds"] = perf_counter() - stage
    report = dict(run_id=run_dir.name, frame_count=len(source.time_values), storage=storage,
                  aggregation_buffer_bytes=0 if totals is None or work is None else totals.nbytes + work.nbytes,
                  intermediate_file_count=len(list(output.glob("frame-*.npz"))),
                  native_resolution_m=source.block_size_m, ranking_resolution_m=1,
                  output_directory=str(output.resolve()), **timings,
                  total_seconds=perf_counter() - started,
                  saved_bytes=sum(p.stat().st_size for p in output.iterdir()), top10=rankings,
                  method="cell-center face interpolation, upwind depth, trapezoid in saved time",
                  limitations="Domain exterior faces excluded; recurrent outflow is counted again")
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", action="append")
    parser.add_argument("--storage", choices=("disk", "memory"), default="disk")
    args = parser.parse_args()
    run_ids = args.run_id or ["ae3ada01-5a24-4096-8090-a0294b84cef0", "9c6771b9-0563-4217-a5df-d31d14b3c46f"]
    output = Path("artifacts/energy-stream-benchmark") / uuid4().hex[:8]
    runs_root = Path(os.environ["LOCALAPPDATA"]) / "urban-pluvial-flood-simulator/runs"
    reports = []
    for run_id in run_ids:
        report = benchmark(runs_root / run_id, output / run_id, storage=args.storage)
        reports.append(report)
        print(json.dumps({key: value for key, value in report.items() if key != "top10"}), flush=True)
    (output / "summary.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
