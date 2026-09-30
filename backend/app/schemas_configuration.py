"""Schemas for operator-supplied plant, zone, and PPE configuration."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:
    from app.services.zone_engine import validate_polygon
except ImportError:
    from backend.app.services.zone_engine import validate_polygon


ZoneType = Literal["RESTRICTED", "HAZARDOUS", "PPE_MANDATORY", "WORK_AREA", "VEHICLE_ZONE", "EXCLUSION_ZONE"]
ThresholdSource = Literal["CONFIGURED", "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"]


class PlantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=1, max_length=50)
    location: str | None = Field(None, max_length=255)
    description: str | None = None


class PlantOut(PlantCreate):
    id: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class AreaCreate(BaseModel):
    plant_id: str
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=1, max_length=50)
    description: str | None = None


class AreaOut(AreaCreate):
    id: str

    model_config = ConfigDict(from_attributes=True)


class ZoneCreate(BaseModel):
    area_id: str
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=1, max_length=50)
    zone_type: ZoneType
    geometry_json: list[list[float]] | None = None

    @field_validator("geometry_json")
    @classmethod
    def validate_geometry(cls, value):
        if value is not None and validate_polygon(value) is None:
            raise ValueError("geometry_json must contain at least three finite coordinate pairs")
        return value


class ZoneOut(ZoneCreate):
    id: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class PPERuleCreate(BaseModel):
    zone_id: str
    ppe_type: str = Field(..., min_length=1, max_length=50)
    is_mandatory: bool
    min_confidence: float | None = Field(None, ge=0, le=1)
    threshold_source: ThresholdSource = "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
    source_reference: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def validate_provenance(self):
        if self.threshold_source == "CONFIGURED" and (self.min_confidence is None or not self.source_reference):
            raise ValueError("Configured thresholds require a value and source reference")
        return self


class PPERuleOut(PPERuleCreate):
    id: str
    validation_status: str

    model_config = ConfigDict(from_attributes=True)
