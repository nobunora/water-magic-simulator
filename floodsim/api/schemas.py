"""API response schemas."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from floodsim.domain.geometry import AnalysisArea, GeoBounds
from floodsim.domain.run_state import RunState


class EngineSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: Literal["SFINCS 2.4.0 Galibier"] = "SFINCS 2.4.0 Galibier"


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    api_version: Literal["v1"] = "v1"
    application_version: str
    engine: EngineSummary


class AppConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["local", "demo"]
    allow_run: bool
    allow_result_import: bool
    download_url: str
    demo_result_event_ids: list[str]


class ApiError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    stage: str | None = None
    retryable: bool = False


class ApiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ApiError


class GeocodeAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    url: str


class GeocodeCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    provider: Literal["csis_simple_geocoding"]
    confidence: int | None = None
    level: int | None = None
    converted: str | None = None


class GeocodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[GeocodeCandidateResponse] = Field(max_length=10)
    attribution: GeocodeAttribution


class RainfallStationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_id: str
    name: str
    prefecture_or_region: str | None = None
    lon_deg: float = Field(ge=-180, le=180)
    lat_deg: float = Field(ge=-90, le=90)
    distance_km: float | None = Field(default=None, ge=0)


class RainfallStationSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stations: list[RainfallStationResponse]


class RainfallEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    station_id: str
    station_name: str
    station_lon_deg: float = Field(ge=-180, le=180)
    station_lat_deg: float = Field(ge=-90, le=90)
    duration_minutes: int = Field(gt=0)
    total_precipitation_mm: float = Field(gt=0)
    rank: int | None = Field(default=None, ge=1, le=10)
    event_date_or_datetime_metadata: str | None = None
    source_url: str
    catalog_generated_at_utc: str
    data_quality_flags: list[str]
    profile_available: bool
    profile_id: str | None = None
    intensity_mm_per_h: float = Field(gt=0)
    damage_location_name: str | None = None
    damage_location_source_url: str | None = None


class RainfallExtremesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station: RainfallStationResponse
    events: list[RainfallEventResponse]


class RecentRainfallRankingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: str
    period_end: str
    coverage_note: str
    events: list[RainfallEventResponse] = Field(max_length=10)


class RunCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: Literal["QUEUED"] = "QUEUED"


class ResultImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: Literal["COMPLETE"] = "COMPLETE"


class RunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    state: RunState
    stage_code: str
    stage_label: str
    failure_code: str | None = None
    failure_message: str | None = None
    progress_fraction: float | None = Field(default=None, ge=0, le=1)
    estimated_remaining_seconds: float | None = Field(default=None, ge=0)
    progress_detail: str | None = None
    activity_lines: list[str] = Field(default_factory=list)


class CancelRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    state: RunState


class ResourceEstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_area: AnalysisArea
    accuracy_mode: Literal["full_1m", "uniform", "adaptive"]
    grid_cell_size_m: Literal[1, 2, 4] = 1


class ResourceEstimateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_1m_equivalent_cells: int
    preliminary_adaptive_cells: int | None = None
    estimated_memory_class: Literal["small", "medium", "heavy", "very_heavy"]
    estimated_disk_class: Literal["small", "medium", "heavy", "very_heavy"]
    runtime_class: Literal["small", "medium", "heavy", "very_heavy"]
    warnings: list[str]


class ElevationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_area: AnalysisArea
    grid_cell_size_m: Literal[1, 2, 4] = 1


class ResultDepthLegendItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    min_m: float = Field(ge=0)
    max_m: float | None = Field(default=None, gt=0)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class ResultElevationLegendItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    min_m: float
    max_m: float
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class ElevationPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: UUID
    bounds: GeoBounds
    grid_cell_size_m: Literal[1, 2, 4]
    width_samples: int = Field(gt=0)
    height_samples: int = Field(gt=0)
    elevation_legend: list[ResultElevationLegendItem]
    provider_counts: dict[str, int] = Field(default_factory=dict)
    nearest_filled_cells: int = Field(ge=0)
    image_url: str


class ResultProviderSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    building_provider: str | None = None
    road_provider: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ResultEngineSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sfincs_version: str | None = None
    sfincs_build_sha256: str | None = None
    sfincs_engine_source: str | None = None
    hydromt_sfincs_version: str | None = None


class ResultRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_version: str
    requested_accuracy_mode: Literal["full_1m", "uniform", "adaptive"]
    rainfall_source: dict[str, Any] = Field(default_factory=dict)
    elevation_provider_counts: dict[str, int] = Field(default_factory=dict)
    elevation_source_summary: dict[str, Any] = Field(default_factory=dict)
    manning_defaults: dict[str, float] = Field(default_factory=dict)
    boundary_policy: str
    roof_rain_mass_diagnostic: dict[str, float | int] = Field(default_factory=dict)


class ResultMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    bounds: GeoBounds
    units: dict[str, str]
    available_time_indices: list[int]
    time_values: list[str]
    flow_vectors_available: bool = False
    storage_kind: str | None = None
    chunk_shape: list[int] | None = None
    cache_schema_revision: int | None = None
    max_depth_summary: dict[str, float]
    grid_level_summary: dict[str, int]
    display_scales: dict[str, dict[str, Any]] = Field(default_factory=dict)
    depth_legend: list[ResultDepthLegendItem] = Field(default_factory=list)
    elevation_legend: list[ResultElevationLegendItem] = Field(default_factory=list)
    provider_summary: ResultProviderSummary = Field(default_factory=ResultProviderSummary)
    engine_summary: ResultEngineSummary = Field(default_factory=ResultEngineSummary)
    run_summary: ResultRunSummary
    no_data_policy: str
    limitations: dict[str, bool]


class ResultExtremeLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, le=10)
    cell_index: int = Field(ge=0)
    lon_deg: float
    lat_deg: float
    time_index: int = Field(ge=0)
    time_value: str
    depth_m: float = Field(ge=0)
    speed_mps: float | None = Field(default=None, ge=0)
    cell_area_m2: float = Field(gt=0)
    total_energy_j: float | None = Field(default=None, ge=0)
    total_outflow_m3: float | None = Field(default=None, ge=0)


class ResultExtremaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depth: list[ResultExtremeLocation]
    speed: list[ResultExtremeLocation]


class ResultExtremaJobResponse(ResultExtremaResponse):
    status: Literal["queued", "running", "complete", "failed"]
    progress_percent: int = Field(ge=0, le=100)
    processed_frames: int = Field(ge=0)
    total_frames: int = Field(ge=0)
    error: str | None = None


class ResultEnergyJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["queued", "running", "complete", "failed"]
    progress_percent: int = Field(ge=0, le=100)
    processed_frames: int = Field(ge=0)
    total_frames: int = Field(ge=0)
    energy: list[ResultExtremeLocation] = Field(default_factory=list)
    aggregation_buffer_bytes: int = Field(default=0, ge=0)
    method: str = ""
    error: str | None = None


class PointEnergyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lon_deg: float = Field(ge=-180, le=180)
    lat_deg: float = Field(ge=-90, le=90)
    has_data: bool
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    cell_area_m2: float = Field(default=1, gt=0)
    total_energy_j: float | None = Field(default=None, ge=0)
    total_outflow_m3: float | None = Field(default=None, ge=0)
    rank: int | None = Field(default=None, ge=1)
    through_time_index: int = Field(ge=0)
    through_time_value: str


class PointInspectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lon_deg: float = Field(ge=-180, le=180)
    lat_deg: float = Field(ge=-90, le=90)
    has_data: bool
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    time_index: int | None = Field(default=None, ge=0)
    time_value: str | None = None
    depth_m: float | None = None
    speed_mps: float | None = Field(default=None, ge=0)
    max_depth_m: float | None = None
    max_time_index: int | None = Field(default=None, ge=0)
    max_time_value: str | None = None
    terrain_elevation_m: float | None = None
    grid_resolution_m: float | None = None


class RunEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    state: RunState
    stage_code: str
    stage_label: str
    progress: float | None = Field(default=None, ge=0, le=1)
    message: str
    timestamp: str


class GenericJsonResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    data: dict[str, Any] = Field(default_factory=dict)
