"""Single-active-run coordinator for the Phase 3 Full 1 m workflow."""

from __future__ import annotations

import inspect
import json
import shutil
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import numpy as np
from platformdirs import user_data_path

from floodsim import __version__
from floodsim.domain.manifest import RunManifest
from floodsim.domain.run_config import AccuracyMode, RunConfig
from floodsim.domain.run_state import RunState, RunStateMachine
from floodsim.orchestration.rainfall_resolution import resolve_rainfall
from floodsim.preprocessing.adaptive_grid import (
    DEFAULT_ADAPTIVE_GRID_POLICY,
    AdaptiveGridPolicy,
    build_adaptive_grid,
)
from floodsim.preprocessing.full_grid import build_full_1m_grid
from floodsim.providers.gsi_elevation import GsiElevationProvider
from floodsim.providers.jma import JmaCatalogProvider
from floodsim.providers.vectors import acquire_vectors
from floodsim.results.archive import (
    NORMALIZED_ARRAYS,
    REGULAR_DESCRIPTOR,
    REGULAR_NETCDF,
    import_result_archive,
)
from floodsim.results.normalize import (
    finalize_regular_netcdf_result,
    normalize_quadtree_result,
    normalize_regular_result,
)
from floodsim.results.regular_netcdf_source import inspect_regular_netcdf_source
from floodsim.sfincs.model_builder import AdaptiveSfincsModelBuilder, SfincsModelBuilder
from floodsim.sfincs.output_reader import read_quadtree_result, read_regular_result
from floodsim.sfincs.runner import (
    ResolvedEngine,
    SfincsProgress,
    SfincsRunCancelled,
    SfincsRunner,
    resolve_sfincs_executable,
)
from floodsim.storage.adaptive_grid_cache import AdaptiveGridCache
from floodsim.storage.prepared_grid_cache import PreparedGridCache
from floodsim.storage.run_store import RunStore


class RunCoordinatorError(RuntimeError):
    code = "INTERNAL_RUN_COORDINATOR_ERROR"
    retryable = False


class RunAlreadyActive(RunCoordinatorError):
    code = "RUN_ALREADY_ACTIVE"


class RunNotFound(RunCoordinatorError):
    code = "RUN_NOT_FOUND"


class AdaptiveNotAvailable(RunCoordinatorError):
    code = "GRID_ADAPTIVE_NOT_AVAILABLE"


class ResultNotReady(RunCoordinatorError):
    code = "RESULT_NOT_READY"


DEFAULT_PLATEAU_REVIEW_BUDGET_S = 20.0
DEFAULT_OSM_REVIEW_BUDGET_S = 30.0
STAGE_LABELS = {
    RunState.CREATED: "実行待機",
    RunState.VALIDATING: "入力を確認中",
    RunState.ACQUIRING_TERRAIN: "標高データを取得中",
    RunState.ACQUIRING_VECTORS: "建物・道路データを取得中",
    RunState.ACQUIRING_RAINFALL: "降雨条件を準備中",
    RunState.PREPROCESSING_TERRAIN: "地形を前処理中",
    RunState.ALLOCATING_ROOF_RAIN: "屋根降雨を再配分中",
    RunState.BUILDING_GRID: "1 m計算格子を構築中",
    RunState.BUILDING_MODEL: "SFINCSモデルを構築中",
    RunState.ENSURING_ENGINE: "SFINCSエンジンを確認中",
    RunState.RUNNING_ENGINE: "SFINCSを実行中",
    RunState.READING_RESULTS: "計算結果を読み込み中",
    RunState.COMPLETE: "完了",
    RunState.FAILED: "失敗",
    RunState.CANCELLING: "キャンセル中",
    RunState.CANCELLED: "キャンセル済み",
}


@dataclass(frozen=True)
class RunEvent:
    sequence: int
    state: RunState
    stage_code: str
    stage_label_ja: str
    message: str
    timestamp_utc: str
    progress: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "state": self.state.value,
            "stage_code": self.stage_code,
            "stage_label": self.stage_label_ja,
            "progress": self.progress,
            "message": self.message,
            "timestamp": self.timestamp_utc,
        }


@dataclass
class RunRecord:
    run_id: UUID
    config: RunConfig
    manifest: RunManifest
    machine: RunStateMachine = field(default_factory=RunStateMachine)
    events: list[RunEvent] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    failure_code: str | None = None
    failure_message: str | None = None
    result_metadata: dict[str, Any] | None = None
    future: Future[None] | None = None
    runner: SfincsRunner | None = None
    progress_fraction: float | None = None
    estimated_remaining_seconds: float | None = None
    progress_detail: str | None = None
    activity_lines: list[str] = field(default_factory=list)
    started_monotonic: float = field(default_factory=time.monotonic)
    stage_started_monotonic: float = field(default_factory=time.monotonic)
    major_phase_started_monotonic: float = field(default_factory=time.monotonic)
    lock: threading.RLock = field(default_factory=threading.RLock)
    last_activity_monotonic: float = field(default_factory=time.monotonic)


class RunCoordinator:
    """Own lifecycle mutation and one background worker for Full 1 m runs."""

    def __init__(
        self,
        *,
        runs_root: str | Path | None = None,
        elevation_provider: Any | None = None,
        vector_acquirer: Callable[..., Any] = acquire_vectors,
        plateau_vector_budget_s: float = DEFAULT_PLATEAU_REVIEW_BUDGET_S,
        osm_vector_budget_s: float = DEFAULT_OSM_REVIEW_BUDGET_S,
        rainfall_resolver: Callable[..., Any] = resolve_rainfall,
        catalog_provider: JmaCatalogProvider | None = None,
        grid_builder: Callable[..., Any] = build_full_1m_grid,
        model_builder: Any | None = None,
        adaptive_enabled: bool = False,
        adaptive_grid_builder: Callable[..., Any] = build_adaptive_grid,
        adaptive_grid_policy: AdaptiveGridPolicy = DEFAULT_ADAPTIVE_GRID_POLICY,
        adaptive_model_builder: Any | None = None,
        engine_resolver: Callable[[], ResolvedEngine] = resolve_sfincs_executable,
        runner_factory: Callable[[], SfincsRunner] = SfincsRunner,
        result_reader: Callable[..., Any] = read_regular_result,
        result_normalizer: Callable[..., Any] = normalize_regular_result,
        adaptive_result_reader: Callable[..., Any] = read_quadtree_result,
        adaptive_result_normalizer: Callable[..., Any] = normalize_quadtree_result,
    ) -> None:
        default_root = user_data_path("urban-pluvial-flood-simulator", appauthor=False) / "runs"
        self.store = RunStore(runs_root or default_root)
        self.elevation_provider = elevation_provider or GsiElevationProvider()
        if plateau_vector_budget_s <= 0 or osm_vector_budget_s <= 0:
            raise ValueError("vector acquisition budgets must be positive")
        self.vector_acquirer = vector_acquirer
        self.plateau_vector_budget_s = plateau_vector_budget_s
        self.osm_vector_budget_s = osm_vector_budget_s
        self.rainfall_resolver = rainfall_resolver
        self.catalog_provider = catalog_provider or JmaCatalogProvider()
        self.grid_builder = grid_builder
        self.model_builder = model_builder or SfincsModelBuilder()
        self.adaptive_grid_enabled = adaptive_enabled
        self.adaptive_grid_builder = adaptive_grid_builder
        self.adaptive_grid_policy = adaptive_grid_policy
        self.adaptive_model_builder = adaptive_model_builder or AdaptiveSfincsModelBuilder(
            cache_root=self.store.root.parent / "cache" / "adaptive_subgrid"
        )
        self.engine_resolver = engine_resolver
        self.runner_factory = runner_factory
        self.result_reader = result_reader
        self.result_normalizer = result_normalizer
        self.adaptive_result_reader = adaptive_result_reader
        self.adaptive_result_normalizer = adaptive_result_normalizer
        self.prepared_cache = PreparedGridCache(self.store.root.parent / "cache")
        self.adaptive_grid_cache = AdaptiveGridCache(self.store.root.parent / "cache")
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="floodsim-run")
        self._records: dict[UUID, RunRecord] = {}
        self._active_run_id: UUID | None = None
        self._lock = threading.RLock()
    @staticmethod
    def _adaptive_policy_for_max_block_size(
        policy: AdaptiveGridPolicy,
        maximum_block_size_m: int,
    ) -> AdaptiveGridPolicy:
        """Limit the largest Adaptive block while retaining protected 1 m cells."""
        active_levels = tuple(
            level for level in policy.active_levels_m if level <= maximum_block_size_m
        )
        if not active_levels or active_levels[-1] != maximum_block_size_m:
            raise ValueError("adaptive_max_block_size_m is not enabled by the grid policy")
        return replace(
            policy,
            active_levels_m=active_levels,
            target_mid_max_resolution_m=min(
                policy.target_mid_max_resolution_m,
                maximum_block_size_m,
            ),
        )

    def _manifest_payload(self, record: RunRecord) -> dict[str, Any]:
        return record.manifest.model_dump(mode="json")

    def _persist_manifest(self, record: RunRecord) -> None:
        self.store.write_manifest(record.run_id, self._manifest_payload(record))

    def _append_event(
        self,
        record: RunRecord,
        state: RunState,
        message: str,
        *,
        progress: float | None = None,
    ) -> None:
        event = RunEvent(
            sequence=len(record.events) + 1,
            state=state,
            stage_code=state.value,
            stage_label_ja=STAGE_LABELS[state],
            message=message,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            progress=progress,
        )
        record.events.append(event)

    def _append_activity(self, record: RunRecord, message: str, *, raw: bool = False) -> None:
        line = message.strip()
        if not line:
            return
        if not raw:
            line = f"[APP] {line}"
        with record.lock:
            record.activity_lines.append(line)
            record.last_activity_monotonic = time.monotonic()

    def _heartbeat_activity(self, record: RunRecord, stop_event: threading.Event) -> None:
        """Keep long opaque library calls visibly alive without changing their work."""
        while not stop_event.wait(1.0):
            with record.lock:
                state = record.machine.state
                silent_seconds = time.monotonic() - record.last_activity_monotonic
            if state in {RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED}:
                return
            if silent_seconds < 10.0:
                continue
            detail = f"{STAGE_LABELS[state]}: 処理継続中（直近更新から約{int(silent_seconds)}秒）"
            with record.lock:
                record.progress_detail = detail
            self._append_activity(record, detail)

    def _append_stage_timing(self, record: RunRecord, *, now: float | None = None) -> None:
        measured_at = time.monotonic() if now is None else now
        with record.lock:
            current_state = record.machine.state
            stage_elapsed = max(0.0, measured_at - record.stage_started_monotonic)
            total_elapsed = max(0.0, measured_at - record.started_monotonic)
            self._append_activity(
                record,
                f"処理時間: {STAGE_LABELS[current_state]} {stage_elapsed:.2f} s / "
                f"トータル {total_elapsed:.2f} s",
            )

    def _finish_major_phase(self, record: RunRecord, phase: str) -> None:
        now = time.monotonic()
        finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with record.lock:
            phase_elapsed = max(0.0, now - record.major_phase_started_monotonic)
            total_elapsed = max(0.0, now - record.started_monotonic)
            self._append_activity(
                record,
                f"{phase} 完了: 終了時間 {finished_at} / "
                f"処理時間 {phase_elapsed:.2f} s / トータル {total_elapsed:.2f} s",
            )
            record.major_phase_started_monotonic = now

    def _set_state(self, record: RunRecord, state: RunState, message: str) -> None:
        now = time.monotonic()
        with record.lock:
            if record.machine.state is not RunState.CREATED:
                self._append_stage_timing(record, now=now)
            record.machine.transition(state)
            record.stage_started_monotonic = now
            record.manifest = record.manifest.model_copy(update={"run_status": state})
            record.progress_fraction = None
            record.estimated_remaining_seconds = None
            record.progress_detail = None
            self._append_event(record, state, message)
            self._append_activity(record, message)
            self._persist_manifest(record)

    def _update_work_progress(
        self,
        record: RunRecord,
        fraction: float,
        detail: str,
        *,
        log: bool = True,
    ) -> None:
        bounded = max(0.0, min(1.0, float(fraction)))
        with record.lock:
            record.progress_fraction = bounded
            record.progress_detail = detail
        if log:
            self._append_activity(record, detail)

    def _acquire_vectors_with_progress(
        self,
        record: RunRecord,
        area: Any,
        **kwargs: Any,
    ) -> Any:
        callback = lambda fraction, detail: self._update_work_progress(
            record, fraction, detail
        )
        signature = inspect.signature(self.vector_acquirer)
        accepts_progress = (
            "progress_callback" in signature.parameters
            or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
        )
        if accepts_progress:
            kwargs["progress_callback"] = callback
        return self.vector_acquirer(area, **kwargs)

    def _build_grid_with_progress(
        self,
        record: RunRecord,
        area: Any,
        elevation: Any,
        vectors: Any,
    ) -> Any:
        callback = lambda fraction, detail: self._update_work_progress(
            record, fraction, detail
        )
        signature = inspect.signature(self.grid_builder)
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        kwargs: dict[str, Any] = {}
        if "progress_callback" in signature.parameters or accepts_kwargs:
            kwargs["progress_callback"] = callback
        if "grid_m" in signature.parameters or accepts_kwargs:
            kwargs["grid_m"] = float(record.config.grid_cell_size_m)
        return self.grid_builder(area, elevation, vectors, **kwargs)

    def _mark_cancelled(self, record: RunRecord, message: str) -> None:
        now = time.monotonic()
        with record.lock:
            if record.machine.state is RunState.CANCELLED:
                return
            if record.machine.state is not RunState.CANCELLING:
                self._append_stage_timing(record, now=now)
                record.machine.transition(RunState.CANCELLING)
                record.manifest = record.manifest.model_copy(update={"run_status": RunState.CANCELLING})
                self._append_event(record, RunState.CANCELLING, message)
            record.machine.transition(RunState.CANCELLED)
            record.manifest = record.manifest.model_copy(update={"run_status": RunState.CANCELLED})
            self._append_event(record, RunState.CANCELLED, "計算をキャンセルしました。")
            self._persist_manifest(record)

    def _check_cancel(self, record: RunRecord) -> None:
        if not record.cancel_event.is_set():
            return
        self._mark_cancelled(record, "キャンセル要求を処理しています。")
        raise SfincsRunCancelled("run cancelled")

    def create_run(self, config: RunConfig) -> RunRecord:
        if (
            config.requested_accuracy_mode is AccuracyMode.ADAPTIVE
            and not self.adaptive_grid_enabled
        ):
            raise AdaptiveNotAvailable(
                "Adaptive mode is implemented but remains disabled until the "
                "Full-vs-Adaptive validation gate is accepted"
            )
        with self._lock:
            if self._active_run_id is not None:
                active = self._records.get(self._active_run_id)
                if active is not None and active.machine.state not in {
                    RunState.COMPLETE,
                    RunState.FAILED,
                    RunState.CANCELLED,
                }:
                    raise RunAlreadyActive("one simulation is already active")
            run_id = uuid4()
            manifest = RunManifest(
                application_version=__version__,
                run_id=run_id,
                created_at_utc=datetime.now(timezone.utc),
                analysis_area=config.analysis_area,
                requested_accuracy_mode=config.requested_accuracy_mode,
                run_status=RunState.CREATED,
            )
            record = RunRecord(run_id=run_id, config=config, manifest=manifest)
            self._records[run_id] = record
            self._active_run_id = run_id
            self.store.write_run_config(run_id, config.model_dump(mode="json"))
            self._append_event(record, RunState.CREATED, "計算を受け付けました。")
            self._append_activity(record, "計算を受け付けました。")
            self._persist_manifest(record)
            record.future = self._executor.submit(self._execute, record)
            return record

    def get(self, run_id: UUID) -> RunRecord:
        with self._lock:
            record = self._records.get(run_id)
        if record is None:
            record = self._restore_persisted_record(run_id)
        if record is None:
            raise RunNotFound(str(run_id))
        return record

    def _restore_persisted_record(self, run_id: UUID) -> RunRecord | None:
        """Restore a terminal run, or make an interrupted worker explicit.

        The executor and provider calls live in this process, so a process
        restart cannot safely resume an in-flight acquisition.  Its manifest
        is still useful: retain it and surface a terminal, actionable state
        instead of reporting that the run ID does not exist.
        """
        manifest_payload = self.store.read_manifest(run_id)
        config_path = self.store.run_dir(run_id) / "run_config.json"
        if manifest_payload is None or not config_path.is_file():
            return None
        try:
            manifest = RunManifest.model_validate(manifest_payload)
            config = RunConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

        state = manifest.run_status
        failure_code = manifest.failure_code
        failure_message = manifest.failure_message
        activity_lines = ["[APP] 保存済みの実行情報を復元しました。"]
        if state not in {RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED}:
            failure_code = "RUN_INTERRUPTED"
            failure_message = (
                "ローカル解析サーバーの再起動により、実行中の処理は停止しました。"
                "条件を確認して新しい解析を開始してください。"
            )
            state = RunState.FAILED
            manifest = manifest.model_copy(
                update={
                    "run_status": state,
                    "failing_stage": manifest.run_status.value,
                    "failure_code": failure_code,
                    "failure_message": failure_message,
                }
            )
            self.store.write_manifest(run_id, manifest.model_dump(mode="json"))
            activity_lines.append(f"[APP] {failure_message}")

        result_metadata: dict[str, Any] | None = None
        if state is RunState.COMPLETE:
            metadata_name = manifest.output_files.get("result_metadata")
            if metadata_name:
                metadata_path = self.store.run_dir(run_id) / "results" / metadata_name
                try:
                    loaded_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_metadata, dict):
                        result_metadata = loaded_metadata
                except (OSError, json.JSONDecodeError):
                    pass

        record = RunRecord(
            run_id=run_id,
            config=config,
            manifest=manifest,
            machine=RunStateMachine(state),
            failure_code=failure_code,
            failure_message=failure_message,
            result_metadata=result_metadata,
            progress_fraction=1.0 if state is RunState.COMPLETE else None,
            progress_detail=("保存済みの解析結果を復元しました。" if state is RunState.COMPLETE else None),
            activity_lines=activity_lines,
        )
        with self._lock:
            existing = self._records.get(run_id)
            if existing is not None:
                return existing
            self._records[run_id] = record
        return record

    def import_result(self, archive_path: Path) -> RunRecord:
        """Register a portable archive as a completed, review-only run."""
        run_id = uuid4()
        run_dir = self.store.run_dir(run_id)
        try:
            config, imported_manifest, metadata = import_result_archive(archive_path, run_dir)
            regular_result = (run_dir / "results" / REGULAR_DESCRIPTOR).is_file()
            imported_outputs = (
                {
                    "result_source": REGULAR_DESCRIPTOR,
                    "sfincs_map_nc": REGULAR_NETCDF,
                    "result_metadata": "result_metadata.json",
                }
                if regular_result
                else {
                    "normalized_arrays": NORMALIZED_ARRAYS,
                    "result_metadata": "result_metadata.json",
                }
            )
            manifest = imported_manifest.model_copy(
                update={
                    "run_id": run_id,
                    "run_status": RunState.COMPLETE,
                    "output_files": imported_outputs,
                }
            )
            record = RunRecord(
                run_id=run_id,
                config=config,
                manifest=manifest,
                machine=RunStateMachine(RunState.COMPLETE),
                result_metadata=metadata,
                progress_fraction=1.0,
                progress_detail="保存済みの解析結果をレビュー用に読み込みました。",
            )
            self._append_event(record, RunState.COMPLETE, "保存済みの解析結果を読み込みました。")
            self._append_activity(record, "保存済みの解析結果を読み込みました。")
            self._persist_manifest(record)
            with self._lock:
                self._records[run_id] = record
            return record
        except Exception:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise

    def cancel(self, run_id: UUID) -> RunRecord:
        record = self.get(run_id)
        with record.lock:
            if record.machine.state in {RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED}:
                return record
            record.cancel_event.set()
            runner = record.runner
        self._mark_cancelled(record, "キャンセルを要求しました。")
        if runner is not None:
            runner.cancel()

        # Cancellation is a user-visible terminal state. Release the admission
        # slot immediately instead of waiting for a cooperative preprocessing or
        # model-build call to return. The cancelled worker may finish cleanup in
        # the background, but it can no longer make this run active again.
        with self._lock:
            if self._active_run_id == run_id:
                self._active_run_id = None
        return record

    def result_metadata(self, run_id: UUID) -> dict[str, Any]:
        record = self.get(run_id)
        with record.lock:
            if record.machine.state is not RunState.COMPLETE or record.result_metadata is None:
                raise ResultNotReady(str(run_id))
            return dict(record.result_metadata)

    def result_arrays_path(self, run_id: UUID) -> Path:
        record = self.get(run_id)
        with record.lock:
            if record.machine.state is not RunState.COMPLETE or record.result_metadata is None:
                raise ResultNotReady(str(run_id))
            filename = record.manifest.output_files.get("normalized_arrays")
        if not filename:
            raise ResultNotReady(str(run_id))
        path = self.store.run_dir(run_id) / "results" / filename
        if not path.is_file():
            raise ResultNotReady(str(run_id))
        return path

    def result_source_path(self, run_id: UUID) -> Path:
        """Return a persisted regular NetCDF descriptor for source-backed viewing."""
        record = self.get(run_id)
        with record.lock:
            if record.machine.state is not RunState.COMPLETE or record.result_metadata is None:
                raise ResultNotReady(str(run_id))
            filename = record.manifest.output_files.get("result_source")
        if not filename:
            raise ResultNotReady(str(run_id))
        path = self.store.run_dir(run_id) / "results" / filename
        if not path.is_file():
            raise ResultNotReady(str(run_id))
        return path

    def events_after(self, run_id: UUID, sequence: int = 0) -> list[RunEvent]:
        record = self.get(run_id)
        with record.lock:
            return [event for event in record.events if event.sequence > sequence]

    @staticmethod
    def _array_summary(values: Any) -> dict[str, Any]:
        array = np.asarray(values)
        summary: dict[str, Any] = {
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "size": int(array.size),
        }
        if array.size and np.issubdtype(array.dtype, np.number):
            finite = np.isfinite(array)
            summary["finite_values"] = int(np.count_nonzero(finite))
            summary["nonfinite_values"] = int(array.size - np.count_nonzero(finite))
            if np.any(finite):
                summary["finite_min"] = float(np.min(array[finite]))
                summary["finite_max"] = float(np.max(array[finite]))
        return summary

    @classmethod
    def _grid_input_diagnostic(
        cls,
        record: RunRecord,
        elevation: Any,
        vectors: Any,
    ) -> dict[str, Any]:
        provenance = getattr(vectors, "provenance", None)
        return {
            "analysis_area": {
                "width_m": record.config.analysis_area.width_m,
                "height_m": record.config.analysis_area.height_m,
                "area_m2": record.config.analysis_area.area_m2,
            },
            "elevation": cls._array_summary(elevation.z),
            "vectors": {
                "provider_id": getattr(provenance, "provider_id", None),
                "buildings": len(getattr(vectors, "buildings", [])),
                "road_lines": len(getattr(vectors, "road_lines", [])),
                "road_polygons": len(getattr(vectors, "road_polygons", [])),
            },
        }

    def _persist_failure_diagnostic(
        self,
        record: RunRecord,
        *,
        failing_state: RunState,
        exc: Exception,
        runtime_diagnostic: dict[str, Any],
    ) -> str | None:
        payload = {
            "run_id": str(record.run_id),
            "stage": failing_state.value,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "runtime": runtime_diagnostic,
        }
        try:
            path = self.store.write_diagnostic(
                record.run_id,
                "failure_diagnostic.json",
                payload,
            )
        except Exception:  # noqa: BLE001
            return None
        return path.relative_to(self.store.run_dir(record.run_id)).as_posix()

    def _execute(self, record: RunRecord) -> None:
        run_root = self.store.ensure_run(record.run_id)
        runtime_diagnostic: dict[str, Any] = {}
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_activity,
            args=(record, heartbeat_stop),
            name=f"floodsim-progress-{record.run_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            mode_label = (
                "Adaptive"
                if record.config.requested_accuracy_mode is AccuracyMode.ADAPTIVE
                else f"均一 {record.config.grid_cell_size_m} m"
            )
            self._set_state(
                record,
                RunState.VALIDATING,
                f"{mode_label}入力条件を検証しています。",
            )
            self._check_cancel(record)

            grid_m = float(record.config.grid_cell_size_m)
            cache_entry = self.prepared_cache.load(record.config.analysis_area, grid_m)
            cache_hit = cache_entry is not None
            self._finish_major_phase(record, "準備")

            self._set_state(
                record,
                RunState.ACQUIRING_TERRAIN,
                "準備済み地図データを再利用しています。" if cache_hit else "地理院標高タイルを取得しています。",
            )
            if cache_entry is not None:
                grid = cache_entry.grid
                cache_metadata = cache_entry.metadata
                runtime_diagnostic["prepared_grid_cache"] = {
                    "hit": True,
                    "key": cache_entry.key,
                }
                self._append_activity(record, f"prepared grid cache hit: {cache_entry.key}")
            else:
                elevation = self.elevation_provider.acquire(
                    record.config.analysis_area,
                    grid_m=grid_m,
                    cache_dir=run_root.parent.parent / "cache",
                )
                runtime_diagnostic["elevation"] = self._array_summary(elevation.z)
                self._append_activity(
                    record,
                    f"標高取得完了: {elevation.z.shape[1]} × {elevation.z.shape[0]} samples",
                )
            self._check_cancel(record)

            self._set_state(
                record,
                RunState.ACQUIRING_VECTORS,
                "準備済み建物・道路データを再利用しています。" if cache_hit else "PLATEAU優先で建物・道路を取得しています。",
            )
            if not cache_hit:
                area_scale = min(
                    16.0,
                    max(1.0, record.config.analysis_area.area_m2 / 250_000.0),
                )
                plateau_budget_s = self.plateau_vector_budget_s * area_scale
                osm_budget_s = self.osm_vector_budget_s * area_scale
                if area_scale > 1.0:
                    self._append_activity(
                        record,
                        "広域解析の取得上限を調整: "
                        f"PLATEAU {plateau_budget_s:.0f}秒 / OSM {osm_budget_s:.0f}秒",
                    )
                vectors = self._acquire_vectors_with_progress(
                    record,
                    record.config.analysis_area,
                    mode="auto",
                    cache_dir=str(run_root.parent.parent / "cache"),
                    out_dir=str(run_root / "source_refs"),
                    plateau_budget_s=plateau_budget_s,
                    osm_budget_s=osm_budget_s,
                    cancel_event=record.cancel_event,
                )
                runtime_diagnostic["grid_input"] = self._grid_input_diagnostic(
                    record,
                    elevation,
                    vectors,
                )
                vector_provenance = vectors.provenance
                self._append_activity(
                    record,
                    "建物・道路取得完了: "
                    f"provider={vector_provenance.provider_id}, "
                    f"building_polygons={len(vectors.buildings)}, "
                    f"road_geometries={len(vectors.road_lines) + len(vectors.road_polygons)}",
                )
            self._check_cancel(record)

            magic = record.config.water_magic
            if magic is not None and magic.release_mode == "initial":
                source_message = "魔法の初期水量・初速と、秒単位の追跡時間を準備しています。"
            elif magic is not None:
                source_message = "魔法の発動・停止・緩和を秒単位の給水系列へ変換しています。"
            else:
                source_message = "降雨シナリオを時間系列へ変換しています。"
            self._set_state(record, RunState.ACQUIRING_RAINFALL, source_message)
            rainfall = self.rainfall_resolver(record.config, self.catalog_provider)
            self._check_cancel(record)
            self._finish_major_phase(record, "データ取得")

            self._set_state(
                record,
                RunState.PREPROCESSING_TERRAIN,
                f"準備済み{grid_m:g} m地形を再利用しています。" if cache_hit else f"{grid_m:g} m地形配列を検証しています。",
            )
            self._check_cancel(record)
            self._set_state(
                record,
                RunState.ALLOCATING_ROOF_RAIN,
                "準備済み屋根雨水重みを再利用しています。" if cache_hit else "建物屋根の降雨量を周辺地表へ保存的に配分します。",
            )
            self._check_cancel(record)
            self._set_state(
                record,
                RunState.BUILDING_GRID,
                f"準備済み均一{grid_m:g} m格子を再利用しています。" if cache_hit else f"均一{grid_m:g} m格子・建物マスク・粗度を構築しています。",
            )

            if cache_hit:
                assert cache_entry is not None
                cache_metadata = cache_entry.metadata
            else:
                grid = self._build_grid_with_progress(
                    record,
                    record.config.analysis_area,
                    elevation,
                    vectors,
                )
                elevation_details = elevation.provenance.source_details
                vector_provenance = vectors.provenance
                cache_metadata = {
                    "elevation_provider_counts": dict(elevation_details.get("provider_counts", {})),
                    "elevation_source_summary": {
                        "grid_m": grid_m,
                        "source_names": list(elevation.source_names),
                        "nearest_filled_cells": elevation.nearest_filled,
                    },
                    "building_provider": vector_provenance.provider_id,
                    "road_provider": vector_provenance.provider_id,
                    "provider_warnings": list(vector_provenance.warnings),
                }
                saved_entry = self.prepared_cache.save(
                    record.config.analysis_area,
                    grid,
                    metadata=cache_metadata,
                )
                runtime_diagnostic["prepared_grid_cache"] = {
                    "hit": False,
                    "key": saved_entry.key,
                }
                self._append_activity(
                    record,
                    f"均一{grid.dx_m:g} m格子構築完了: {grid.width_cells} × {grid.height_cells} cells / "
                    f"building_cells={int(np.count_nonzero(grid.building_mask))}",
                )
                self._append_activity(record, f"prepared grid cache saved: {saved_entry.key}")

            elevation_summary = dict(cache_metadata.get("elevation_source_summary", {}))
            elevation_summary.update(
                {
                    "prepared_cache_hit": cache_hit,
                    "prepared_cache_key": self.prepared_cache.key_for(record.config.analysis_area, grid_m),
                }
            )
            adaptive_grid = None
            final_grid_level_counts = {f"{grid.dx_m:g}m": grid.cell_count}
            if record.config.requested_accuracy_mode is AccuracyMode.ADAPTIVE:
                adaptive_policy = self._adaptive_policy_for_max_block_size(
                    self.adaptive_grid_policy,
                    record.config.adaptive_max_block_size_m,
                )
                prepared_grid_key = self.prepared_cache.key_for(record.config.analysis_area, grid_m)
                cached_adaptive = (
                    self.adaptive_grid_cache.load(prepared_grid_key, adaptive_policy)
                    if self.adaptive_grid_builder is build_adaptive_grid
                    else None
                )
                if cached_adaptive is not None:
                    adaptive_cache_key, adaptive_grid = cached_adaptive
                    self._append_activity(record, f"Adaptive格子分類 cache hit: {adaptive_cache_key}")
                    runtime_diagnostic["adaptive_grid_cache"] = {"hit": True, "key": adaptive_cache_key}
                else:
                    self._update_work_progress(
                        record,
                        0.0,
                        "Adaptive格子の地形複雑度を分類しています。",
                    )
                    adaptive_signature = inspect.signature(self.adaptive_grid_builder)
                    accepts_kwargs = any(
                        parameter.kind is inspect.Parameter.VAR_KEYWORD
                        for parameter in adaptive_signature.parameters.values()
                    )
                    adaptive_kwargs: dict[str, Any] = {}
                    adaptive_inputs = {
                        "policy": adaptive_policy,
                        "hard_boundary_zone": grid.adaptive_hard_boundary_zone,
                        "existing_resolution_ceiling_m": grid.adaptive_resolution_ceiling_m,
                        "native_structure_mask": grid.native_structure_mask,
                        "progress_callback": lambda fraction, detail: self._update_work_progress(
                            record, fraction, detail
                        ),
                    }
                    for name, value in adaptive_inputs.items():
                        if accepts_kwargs or name in adaptive_signature.parameters:
                            adaptive_kwargs[name] = value
                    adaptive_grid = self.adaptive_grid_builder(grid, **adaptive_kwargs)
                    if self.adaptive_grid_builder is build_adaptive_grid:
                        adaptive_cache_key = self.adaptive_grid_cache.save(
                            prepared_grid_key, adaptive_policy, adaptive_grid
                        )
                        runtime_diagnostic["adaptive_grid_cache"] = {"hit": False, "key": adaptive_cache_key}
                        self._append_activity(record, f"Adaptive格子分類 cache saved: {adaptive_cache_key}")
                final_grid_level_counts = dict(adaptive_grid.cell_count_by_level)
                runtime_diagnostic["adaptive_grid"] = dict(adaptive_grid.diagnostics)
                self._append_activity(
                    record,
                    "Adaptive格子分類完了: "
                    f"{adaptive_grid.total_hydraulic_cells} hydraulic cells / "
                    f"Full 1 m比 {adaptive_grid.reduction_ratio:.4f}",
                )
                self._check_cancel(record)

            record.manifest = record.manifest.model_copy(
                update={
                    "projected_crs": grid.crs_wkt,
                    "final_grid_level_counts": final_grid_level_counts,
                    "elevation_provider_counts": dict(cache_metadata.get("elevation_provider_counts", {})),
                    "elevation_source_summary": elevation_summary,
                    "building_provider": cache_metadata.get("building_provider"),
                    "road_provider": cache_metadata.get("road_provider"),
                    "provider_warnings": list(cache_metadata.get("provider_warnings", [])),
                    "rainfall_source": dict(rainfall.source_metadata),
                    "roof_rain_mass_diagnostic": {
                        "relative_error": grid.roof_allocation.relative_mass_error,
                        "meteorological_area_m2": grid.roof_allocation.meteorological_area_m2,
                        "hydraulic_weighted_area_m2": grid.roof_allocation.hydraulic_weighted_area_m2,
                        "building_components": grid.roof_allocation.building_components,
                    },
                }
            )
            self._persist_manifest(record)
            self._check_cancel(record)

            if record.config.requested_accuracy_mode is AccuracyMode.ADAPTIVE:
                assert adaptive_grid is not None
                self._set_state(
                    record,
                    RunState.BUILDING_MODEL,
                    "HydroMT-SFINCSでAdaptive quadtree/subgridモデルを構築しています。",
                )
                build = self.adaptive_model_builder.build(
                    run_root / "model",
                    grid,
                    adaptive_grid,
                    rainfall,
                )
                static_cache = build.report.get("static_model_cache", {})
                if static_cache:
                    hit_label = "HIT" if static_cache.get("cache_hit") else "MISS"
                    cache_key_label = static_cache.get("cache_key", "unknown")
                    self._append_activity(
                        record,
                        f"Adaptive static model cache {hit_label}: {cache_key_label}",
                    )
                build_timings = build.report.get("build_phase_timings_seconds", {})
                for phase_name, seconds in build_timings.items():
                    self._append_activity(
                        record,
                        f"Adaptive build {phase_name}: {float(seconds):.2f} s",
                    )
            else:
                self._set_state(
                    record,
                    RunState.BUILDING_MODEL,
                    f"HydroMT-SFINCSで均一{grid.dx_m:g} mモデルを構築しています。",
                )
                build = self.model_builder.build(run_root / "model", grid, rainfall)
            if record.config.water_magic is not None:
                record.manifest = record.manifest.model_copy(update={"rainfall_source": dict(rainfall.source_metadata)})
                self._persist_manifest(record)
            self._append_activity(record, "SFINCSモデル構築完了。")
            self._check_cancel(record)

            self._set_state(record, RunState.ENSURING_ENGINE, "SFINCS 2.4.0 Galibierを確認しています。")
            engine = self.engine_resolver()
            record.manifest = record.manifest.model_copy(
                update={
                    "sfincs_version": engine.version,
                    "sfincs_build_sha256": engine.sha256,
                    "sfincs_engine_source": engine.source,
                    "hydromt_sfincs_version": "2.0.0rc3",
                }
            )
            self._persist_manifest(record)
            self._check_cancel(record)
            self._finish_major_phase(record, "解析格子")

            self._set_state(record, RunState.RUNNING_ENGINE, "SFINCSを実行しています。")
            runner = self.runner_factory()
            with record.lock:
                record.runner = runner
                cancelled_before_runner_registration = record.cancel_event.is_set()
            if cancelled_before_runner_registration:
                self._check_cancel(record)
            engine_started = time.monotonic()

            def append_engine_line(line: str) -> None:
                self._append_activity(record, line, raw=True)

            def update_engine_progress(progress: SfincsProgress) -> None:
                elapsed = max(0.0, time.monotonic() - engine_started)
                remaining = None
                if progress.fraction > 0.0:
                    estimated_total = elapsed / progress.fraction
                    remaining = max(0.0, estimated_total - elapsed)
                with record.lock:
                    record.progress_fraction = progress.fraction
                    record.estimated_remaining_seconds = remaining
                    record.progress_detail = (
                        f"SFINCS {round(progress.fraction * 100)}% / "
                        f"残り目安 {remaining:.1f}秒"
                        if remaining is not None
                        else f"SFINCS {round(progress.fraction * 100)}%"
                    )

            execution = runner.run(
                build.model_dir,
                logs_dir=run_root / "logs",
                engine=engine,
                cancel_event=record.cancel_event,
                progress_callback=update_engine_progress,
                line_callback=append_engine_line,
            )
            with record.lock:
                record.progress_fraction = 1.0
                record.estimated_remaining_seconds = 0.0
                record.progress_detail = "SFINCS 100%"
            runtime_diagnostic["sfincs_elapsed_seconds"] = execution.elapsed_seconds
            self._append_activity(record, f"SFINCS完了: {execution.elapsed_seconds:.2f} s")
            record.runner = None
            self._check_cancel(record)
            self._finish_major_phase(record, "SFINCS")

            self._set_state(
                record,
                RunState.READING_RESULTS,
                "SFINCS NetCDF結果を正規化しています。",
            )
            normalizer = self.result_normalizer
            if record.config.requested_accuracy_mode is AccuracyMode.ADAPTIVE:
                if build.adaptive_layout_path is None:
                    raise RuntimeError("Adaptive model build did not persist face layout")
                raw_result = self.adaptive_result_reader(
                    execution.result_path,
                    layout_path=build.adaptive_layout_path,
                )
                normalizer = self.adaptive_result_normalizer
            else:
                source = inspect_regular_netcdf_source(
                    execution.result_path,
                    model_dir=execution.result_path.parent,
                    bounds=record.config.analysis_area.bounds.model_dump(),
                    block_size_m=grid.dx_m,
                    display_dry_threshold_m=0.000001 if record.config.water_magic else 0.01,
                )

            provider_summary: dict[str, Any] = {
                "building_provider": record.manifest.building_provider,
                "road_provider": record.manifest.road_provider,
                "warnings": list(record.manifest.provider_warnings),
            }
            engine_summary: dict[str, Any] = {
                "sfincs_version": record.manifest.sfincs_version,
                "sfincs_build_sha256": record.manifest.sfincs_build_sha256,
                "sfincs_engine_source": record.manifest.sfincs_engine_source,
                "hydromt_sfincs_version": record.manifest.hydromt_sfincs_version,
            }
            run_summary: dict[str, Any] = {
                "application_version": record.manifest.application_version,
                "requested_accuracy_mode": record.manifest.requested_accuracy_mode.value,
                "rainfall_source": dict(record.manifest.rainfall_source),
                "elevation_provider_counts": dict(record.manifest.elevation_provider_counts),
                "elevation_source_summary": dict(record.manifest.elevation_source_summary),
                "manning_defaults": dict(record.manifest.manning_defaults),
                "boundary_policy": record.manifest.boundary_policy,
                "roof_rain_mass_diagnostic": dict(record.manifest.roof_rain_mass_diagnostic),
            }
            normalizer_args = {
                "area": record.config.analysis_area,
                "results_dir": run_root / "results",
                "limitations": record.manifest.limitations,
                "provider_summary": provider_summary,
                "engine_summary": engine_summary,
                "run_summary": run_summary,
            }
            if record.config.requested_accuracy_mode is AccuracyMode.ADAPTIVE:
                normalized = normalizer(raw_result, **normalizer_args)
            else:
                normalized = finalize_regular_netcdf_result(
                    source,
                    model_dir=execution.result_path.parent,
                    results_dir=run_root / "results",
                    limitations=record.manifest.limitations,
                    provider_summary=provider_summary,
                    engine_summary=engine_summary,
                    run_summary=run_summary,
                )
            record.result_metadata = normalized.metadata
            self._append_activity(record, "結果読込・正規化完了。")
            output_files = {
                "sfincs_map_nc": execution.result_path.name,
                "model_build_report": build.report_path.name,
                "result_metadata": normalized.metadata_path.name,
            }
            if record.config.requested_accuracy_mode is AccuracyMode.ADAPTIVE:
                output_files["normalized_arrays"] = normalized.arrays_path.name
            else:
                output_files["result_source"] = normalized.descriptor_path.name
            record.manifest = record.manifest.model_copy(
                update={
                    "output_files": output_files
                }
            )
            self._persist_manifest(record)
            self._finish_major_phase(record, "結果表示")
            self._set_state(record, RunState.COMPLETE, f"{mode_label}計算が完了しました。")
        except SfincsRunCancelled:
            self._mark_cancelled(record, "キャンセル要求を処理しています。")
        # Top-level worker boundary: persist unexpected operational failures as FAILED.
        except Exception as exc:  # noqa: BLE001
            with record.lock:
                if record.cancel_event.is_set():
                    self._mark_cancelled(record, "キャンセル要求を処理しています。")
                else:
                    failing_state = record.machine.state
                    self._append_stage_timing(record)
                    record.machine.transition(RunState.FAILED)
                    code = str(getattr(exc, "code", "INTERNAL_RUN_FAILED"))
                    message = str(exc) or type(exc).__name__
                    diagnostic_file = self._persist_failure_diagnostic(
                        record,
                        failing_state=failing_state,
                        exc=exc,
                        runtime_diagnostic=runtime_diagnostic,
                    )
                    record.failure_code = code
                    record.failure_message = message
                    record.manifest = record.manifest.model_copy(
                        update={
                            "run_status": RunState.FAILED,
                            "failing_stage": failing_state.value,
                            "failure_code": code,
                            "failure_exception_type": type(exc).__name__,
                            "failure_message": message,
                            "failure_diagnostic_file": diagnostic_file,
                        }
                    )
                    self._append_event(record, RunState.FAILED, "計算に失敗しました。")
                self._persist_manifest(record)
        finally:
            heartbeat_stop.set()
            record.runner = None
            with self._lock:
                if self._active_run_id == record.run_id:
                    self._active_run_id = None
