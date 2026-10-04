"""Bounded, serialized ranking jobs with frame-based progress snapshots.

Energy jobs additionally retain their aggregate buffer for point inspection.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

import numpy as np

Progress = Callable[[int, int], None]
Calculation = Callable[[Progress], dict[str, Any]]
logger = logging.getLogger(__name__)


class EnergyJobs:
    def __init__(self, *, result_fields: tuple[str, ...] = ("energy",),
                 failure_message: str = "エネルギー集計に失敗しました。") -> None:
        self._lock = Lock()
        self._result_fields = result_fields
        self._failure_message = failure_message
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="outflow-energy")
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._totals: dict[str, np.ndarray] = {}

    def start(self, key: str, total: int, calculate: Calculation) -> dict[str, Any]:
        with self._lock:
            if key in self._jobs and self._jobs[key]["status"] != "failed":
                self._jobs.move_to_end(key)
                return dict(self._jobs[key])
            self._jobs.pop(key, None)
            self._totals.pop(key, None)
            if len(self._jobs) >= 16:
                finished = next((k for k, v in self._jobs.items()
                                 if v["status"] in ("complete", "failed")), None)
                if finished is None:
                    raise ValueError("集計待ちが多いため、しばらく待ってから再実行してください。")
                del self._jobs[finished]
                self._totals.pop(finished, None)
            state: dict[str, Any] = {"status": "queued", "progress_percent": 0,
                                     "processed_frames": 0, "total_frames": total,
                                     **{field: [] for field in self._result_fields}}
            self._jobs[key] = state
            snapshot = dict(state)
            self._executor.submit(self._calculate, key, calculate)
            return snapshot

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._jobs.get(key)
            return None if state is None else dict(state)

    def _calculate(self, key: str, calculate: Calculation) -> None:
        def progress(done: int, total: int) -> None:
            with self._lock:
                self._jobs[key].update(status="running", processed_frames=done,
                                       total_frames=total,
                                       progress_percent=min(99, int(100 * done / max(total, 1))))

        with self._lock:
            self._jobs[key]["status"] = "running"
        try:
            result = calculate(progress)
        except Exception as exc:
            logger.exception("Result ranking calculation failed")
            message = str(exc) if isinstance(exc, ValueError) else self._failure_message
            with self._lock:
                self._jobs[key].update(status="failed", error=message)
        else:
            totals = result.pop("_totals", None)
            with self._lock:
                if totals is not None:
                    self._totals[key] = totals
                self._jobs[key].update(result, status="complete", progress_percent=100)

    def point(self, key: str, row: int, col: int) -> dict[str, Any] | None:
        with self._lock:
            totals = self._totals.get(key)
            if totals is None:
                return None
            if not (0 <= row < totals.shape[1] and 0 <= col < totals.shape[2]):
                raise ValueError("point is outside the energy grid")
            volume, energy = float(totals[0, row, col]), float(totals[1, row, col])
            if not np.isfinite(energy):
                return {"has_data": False}
            flat = totals[1].ravel()
            cell = row * totals.shape[2] + col
            rank = int(1 + np.count_nonzero(flat > energy) + np.count_nonzero(flat[:cell] == energy)) if energy > 0 else None
            return {"has_data": True, "total_energy_j": energy,
                    "total_outflow_m3": volume, "rank": rank}

    def close(self) -> None:
        self._executor.shutdown(wait=True)
