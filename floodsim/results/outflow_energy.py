"""One-metre boundary outflow energy, integrated one retained frame at a time."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
from pyproj import CRS, Transformer

from floodsim.domain.geometry import AnalysisArea
from floodsim.providers.common import local_crs
from floodsim.results.extrema import _locate
from floodsim.results.regular_netcdf_source import (
    RegularNetcdfSource,
    validate_source_identity,
)
from floodsim.results.regular_queries import summary_directory
from floodsim.storage.run_store import atomic_write_json


def outflow_rates(h, u, v, active, cell_size: float):
    """Return m³/s and J/s per 1m block; exclude internal subcell faces."""
    factor = round(1 / cell_size)
    if factor < 1 or not np.isclose(factor * cell_size, 1):
        raise ValueError("Only native regular resolution dividing 1m is supported")
    rows, cols = h.shape
    if rows % factor or cols % factor:
        raise ValueError("The grid must consist of complete 1m blocks")
    valid = active & np.isfinite(h) & np.isfinite(u) & np.isfinite(v)
    h = np.maximum(np.nan_to_num(h), 0)
    volume = np.zeros((rows // factor, cols // factor), dtype=np.float64)
    power = np.zeros_like(volume)
    for axis in (1, 0):
        first = [slice(None), slice(None)]
        second = [slice(None), slice(None)]
        first[axis] = slice(factor - 1, -1, factor)
        second[axis] = slice(factor, None, factor)
        left, right = tuple(first), tuple(second)
        face_u, face_v = (u[left] + u[right]) * .5, (v[left] + v[right]) * .5
        normal = face_u if axis == 1 else face_v
        positive = normal >= 0
        donor_depth = np.where(positive, h[left], h[right])
        q = np.where(valid[left] & valid[right], np.abs(normal) * donor_depth * cell_size, 0)
        energy = np.where(q > 0, 500 * q * (face_u**2 + face_v**2), 0)
        for forward, target in ((True, slice(None, -1)), (False, slice(1, None))):
            directed_q = np.where(positive == forward, q, 0)
            directed_e = np.where(positive == forward, energy, 0)
            if axis == 1:
                volume[:, target] += directed_q.reshape(rows // factor, factor, -1).sum(axis=1)
                power[:, target] += directed_e.reshape(rows // factor, factor, -1).sum(axis=1)
            else:
                volume[target, :] += directed_q.reshape(-1, cols // factor, factor).sum(axis=2)
                power[target, :] += directed_e.reshape(-1, cols // factor, factor).sum(axis=2)
    return volume, power


def accumulate_interval(totals, work, q, p, dt):
    """Reuse previous rates and interval scratch space without retaining frames."""
    np.add(work[0], q, out=work[2])
    np.add(work[1], p, out=work[3])
    work[2:4] *= .5 * dt
    np.add(totals, work[2:4], out=totals)
    np.copyto(work[0], q)
    np.copyto(work[1], p)


def regular_energy(
    source: RegularNetcdfSource, *, model_dir: Path, area: AnalysisArea,
    times: list[int], progress: Callable[[int, int], None],
    retain_totals: bool = False,
) -> dict[str, Any]:
    path = validate_source_identity(source, model_dir=model_dir)
    if not source.flow_vectors_available or not times:
        raise ValueError("流速を持つ保存済み時刻が必要です。")
    if times != sorted(set(times)) or any(t < 0 or t >= len(source.time_values) for t in times):
        raise ValueError("保存済みの測定時刻を昇順で指定してください。")
    identity = json.dumps({"times": times, "area": area.model_dump()}, sort_keys=True)
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    cache = summary_directory(source, model_dir) / f"outflow-energy-v1-{digest}.json"
    if cache.is_file() and not retain_totals:
        result = json.loads(cache.read_text(encoding="utf-8"))
        progress(len(times), len(times))
        return result
    totals = work = None
    previous_time = None
    with xr.open_dataset(path) as dataset:
        active = np.asarray(dataset["msk"].values) > 0
        for done, index in enumerate(times, 1):
            h, u, v = (np.asarray(dataset[key].isel(time=index).values, dtype=np.float64)
                       for key in ("h", "u", "v"))
            timestamp = dataset.time.values[index]
            q, power = outflow_rates(h, u, v, active, source.block_size_m)
            if totals is None:
                totals = np.zeros((2, *q.shape), dtype=np.float64)
                work = np.zeros((4, *q.shape), dtype=np.float64)
                work[0], work[1] = q, power
            else:
                assert work is not None and previous_time is not None
                difference = timestamp - previous_time
                dt = float(difference / np.timedelta64(1, "s")) if np.issubdtype(dataset.time.dtype, np.datetime64) else float(difference)
                if not np.isfinite(dt) or dt <= 0:
                    raise ValueError("測定時刻は時間順に並んでいる必要があります。")
                accumulate_interval(totals, work, q, power, dt)
            previous_time = timestamp
            progress(done, len(times))
    assert totals is not None and work is not None
    volume, energy = totals
    factor = round(1 / source.block_size_m)
    block_active = active.reshape(source.height // factor, factor, source.width // factor, factor).any(axis=(1, 3))
    energy[~block_active] = np.nan
    cells = np.flatnonzero(energy.ravel() > 0)
    if cells.size > 10:
        cutoff = np.partition(energy.ravel()[cells], -10)[-10]
        cells = cells[energy.ravel()[cells] >= cutoff]
    cells = cells[np.lexsort((cells, -energy.ravel()[cells]))[:10]]
    transformer = Transformer.from_crs(local_crs(area), CRS.from_epsg(4326), always_xy=True)
    entries = []
    for rank, cell in enumerate(cells, 1):
        row, col = np.unravel_index(cell, energy.shape)
        r, c = row * factor + factor // 2, col * factor + factor // 2
        entry = {"rank": rank, "cell_index": int(cell), "time_index": times[-1],
                 "depth_m": float(max(h[r, c], 0)) if np.isfinite(h[r, c]) else 0.,
                 "speed_mps": float(np.hypot(u[r, c], v[r, c])) if np.isfinite(u[r, c]) and np.isfinite(v[r, c]) else None,
                 "total_outflow_m3": float(volume[row, col]),
                 "total_energy_j": float(energy[row, col])}
        entries.append(_locate(entry, row=row + .5, col=col + .5,
                       shape=energy.shape, area=area, transformer=transformer,
                       cell_area=1., times=source.time_values))
    result = {"energy": entries, "aggregation_buffer_bytes": totals.nbytes + work.nbytes,
              "method": "1 m区画の外向き流量と運動エネルギーを保存時刻間で台形積分。外周面は除外。循環による再流出は再度加算。最終測定時刻までの累積値。"}
    atomic_write_json(cache, result)
    if retain_totals:
        result["_totals"] = totals
    return result
