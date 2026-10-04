"""Versioned, second-based water-source configuration, separate from rainfall."""

from dataclasses import dataclass
from datetime import datetime
from math import gcd
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from floodsim.domain.geometry import LonLat


def magic_output_interval(casting_seconds: int, relaxation_seconds: int) -> float:
    """Include cast/end boundaries and at least three casting observations."""
    common = gcd(casting_seconds, relaxation_seconds)
    total = casting_seconds + relaxation_seconds
    target = 10 if casting_seconds >= 300 else 0.2
    candidates: list[float] = [
        step for step in range(1, common + 1)
        if common % step == 0 and total // step + 1 <= 600
        and (step <= casting_seconds / 3 or casting_seconds < 3)
    ]
    if casting_seconds < 300:
        # Integer cast/end boundaries align with these fractional-second steps.
        candidates.extend(step for step in [0.2, 0.5] if round(total / step) + 1 <= 600)
    if not candidates:
        raise ValueError("魔法継続中の出力と600フレーム上限を両立できません。観測時間を短くしてください。")
    return min(candidates, key=lambda step: (abs(step - target), step))


class WaterMagicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal["1"] = "1"
    spell_id: Literal["healing-rain", "gw2-healingrain", "ff3-tsunami", "warcraft1-elemental",
        "chrono-water2", "ff7-tidalwave", "ro-waterball5", "warcraft3-elemental",
        "tales-tidalwave", "lol-nami-wave", "dos2-rain", "kh3-waterga", "genshin-mona",
        "genshin-neuvillette", "forspoken-cataract", "bg3-createwater", "wow-elemental",
        "dq7-maelstrom", "ff6-flood", "ff15-tsunami", "chrono-cross-deluge",
        "goldensun-neptune", "rs3-maelstrom", "school-pool", "kaiju-godzilla-wave",
        "kaiju-titanosaurus-vortex"] = "healing-rain"
    catalog_revision: Literal["2026-10-03"] = "2026-10-03"
    footprint_kind: Literal["circle", "domain", "rectangle", "sector"] = "circle"
    position: LonLat
    radius_m: float = Field(gt=0, le=4000)
    length_m: float = Field(default=20, gt=0, le=4000)
    width_m: float = Field(default=10, gt=0, le=4000)
    sector_angle_deg: float = Field(default=90, gt=0, le=360)
    bearing_deg: float = Field(ge=0, le=360)
    volume_m3: float = Field(gt=0, le=1_000_000)
    generation_rate_m3ps: float | None = Field(default=None, gt=0, le=1_000_000)
    casting_seconds: int = Field(ge=1, le=3600)
    relaxation_seconds: int = Field(ge=0, le=36000)

    release_mode: Literal["continuous", "initial"] = "continuous"
    initial_motion: Literal["none", "directional", "radial", "vortex"] = "none"
    initial_speed_mps: float = Field(default=0, ge=0, le=4)
    vortex_direction: Literal["clockwise", "counterclockwise"] = "clockwise"
    vortex_core_radius_m: float = Field(default=2, gt=0, le=4000)

    @model_validator(mode="after")
    def validate_output_budget(self) -> "WaterMagicConfig":
        if self.generation_rate_m3ps is not None:
            generated = self.generation_rate_m3ps * self.casting_seconds
            if generated > 1_000_000:
                raise ValueError("生成水量×魔法継続時間の総水量は100万m³以下にしてください。")
            self.volume_m3 = generated
        magic_output_interval(self.casting_seconds, self.relaxation_seconds)
        if self.release_mode == "continuous" and (self.initial_motion != "none" or self.initial_speed_mps != 0):
            raise ValueError("初速は開始時に全量を配置する場合のみ指定できます。")
        if self.initial_motion == "none" and self.initial_speed_mps != 0:
            raise ValueError("初速を指定する場合は運動の種類を選択してください。")
        return self

    @property
    def transition_seconds(self) -> float:
        # Explicit terminal ramp: at most 1% of cast duration / 0.1 seconds.
        # Normalization preserves volume; plateau-rate adjustment is <=0.503%.
        return min(0.1, self.casting_seconds / 100)


@dataclass
class MagicTimeSeries:
    start_time: datetime
    elapsed_seconds: list[float]
    source_metadata: dict[str, str]
    config: WaterMagicConfig
