"""Run configuration contract."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from floodsim.domain.geometry import AnalysisArea
from floodsim.domain.rainfall import RainfallScenario
from floodsim.domain.water_magic import WaterMagicConfig


class AccuracyMode(str, Enum):
    FULL_1M = "full_1m"
    UNIFORM = "uniform"
    ADAPTIVE = "adaptive"


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_area: AnalysisArea
    requested_accuracy_mode: AccuracyMode
    grid_cell_size_m: float = Field(default=1, json_schema_extra={"enum": [0.5, 1, 2, 4]})
    adaptive_max_block_size_m: Literal[1, 2, 4] = 4
    rainfall: RainfallScenario | None = None
    water_magic: WaterMagicConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def default_magic_grid(cls, value):
        if isinstance(value, dict) and value.get("water_magic") is not None and "grid_cell_size_m" not in value:
            return {**value, "grid_cell_size_m": 0.5}
        return value

    @field_validator("grid_cell_size_m")
    @classmethod
    def validate_grid_size(cls, value: float) -> float:
        if value not in (0.5, 1, 2, 4):
            raise ValueError("grid size must be 0.5, 1, 2, or 4 metres")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "RunConfig":
        if (self.rainfall is None) == (self.water_magic is None):
            raise ValueError("specify exactly one of rainfall or water_magic")
        if self.water_magic is not None and (
            self.requested_accuracy_mode is not AccuracyMode.FULL_1M
            or self.grid_cell_size_m not in (0.5, 1)
        ):
            raise ValueError("water magic requires Full 1 m mode with a 0.5 m or legacy 1 m grid")
        if self.water_magic is None and self.grid_cell_size_m == 0.5:
            raise ValueError("0.5 m grid is supported only for water magic")
        if self.water_magic is not None and self.analysis_area.area_m2 / self.grid_cell_size_m**2 > 1_000_000:
            raise ValueError("魔法解析は100万格子までです。0.5 m解析では範囲を±250 m以下にしてください。")
        return self
