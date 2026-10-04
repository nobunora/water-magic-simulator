"""Explicit geographic and projected geometry contracts."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PRESET_HALF_SIZES_M = frozenset({100, 250, 500, 1000, 2000, 4000})


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def reject_non_finite_numbers(cls, value: object) -> object:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("coordinate values must be finite")
        return value


class LonLat(DomainModel):
    lon_deg: float = Field(ge=-180, le=180)
    lat_deg: float = Field(ge=-90, le=90)


class GeoBounds(DomainModel):
    west_deg: float
    south_deg: float
    east_deg: float
    north_deg: float

    @model_validator(mode="after")
    def validate_order(self) -> GeoBounds:
        if self.west_deg >= self.east_deg:
            raise ValueError("west_deg must be less than east_deg")
        if self.south_deg >= self.north_deg:
            raise ValueError("south_deg must be less than north_deg")
        return self


class ProjectedBounds(DomainModel):
    xmin_m: float
    ymin_m: float
    xmax_m: float
    ymax_m: float
    crs: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_order(self) -> ProjectedBounds:
        if self.xmin_m >= self.xmax_m:
            raise ValueError("xmin_m must be less than xmax_m")
        if self.ymin_m >= self.ymax_m:
            raise ValueError("ymin_m must be less than ymax_m")
        return self


class AnalysisArea(DomainModel):
    mode: Literal["preset_square", "rectangle"]
    bounds: GeoBounds
    center: LonLat
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    area_m2: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_shape(self) -> AnalysisArea:
        if self.mode == "preset_square":
            if self.width_m != self.height_m:
                raise ValueError("preset_square must have equal width and height")
            if self.width_m / 2 not in PRESET_HALF_SIZES_M:
                raise ValueError("preset_square half size is not supported")
        if not (self.bounds.west_deg <= self.center.lon_deg <= self.bounds.east_deg):
            raise ValueError("center longitude must be inside bounds")
        if not (self.bounds.south_deg <= self.center.lat_deg <= self.bounds.north_deg):
            raise ValueError("center latitude must be inside bounds")
        expected_area = self.width_m * self.height_m
        if not math.isclose(self.area_m2, expected_area, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError("area_m2 must equal width_m multiplied by height_m")
        return self
