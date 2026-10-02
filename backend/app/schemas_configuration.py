"""Schemas for operator-supplied plant, zone, and PPE configuration."""
from datetime import datetime
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


# ---------------------------------------------------------------------------
# Detector configuration, safety rules, operating thresholds
# ---------------------------------------------------------------------------

DetectorKey = Literal[
    "PPE",
    "HELMET",
    "SAFETY_VEST",
    "RESTRICTED_ZONE",
    "PROXIMITY",
    "FALL",
    "FIRE",
    "SMOKE",
    "LEAKAGE",
    "UNSAFE_BEHAVIOR",
    "PERSON",
]
Comparison = Literal["GT", "GTE", "LT", "LTE", "EQ"]


class DetectorConfigCreate(BaseModel):
    """An operator's request to run one detector. It is not evidence it runs."""

    detector_key: DetectorKey
    camera_id: str | None = None
    zone_id: str | None = None
    is_enabled: bool = False
    parameters_json: dict | None = None
    required_classes_json: list[str] | None = None
    threshold_source: ThresholdSource = "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
    source_reference: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def validate_scope_and_provenance(self):
        if not self.camera_id and not self.zone_id:
            raise ValueError("A detector configuration must target a camera or a zone")
        if self.threshold_source == "CONFIGURED" and not self.source_reference:
            raise ValueError("Configured thresholds require a source reference")
        return self


class DetectorConfigOut(BaseModel):
    id: str
    detector_key: str
    camera_id: str | None = None
    zone_id: str | None = None
    is_enabled: bool
    parameters_json: dict | None = None
    required_classes_json: list | None = None
    threshold_source: str
    source_reference: str | None = None
    validation_status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SafetyRuleCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50, pattern=r"^[A-Z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=150)
    description: str | None = None
    category: str | None = Field(None, max_length=50)
    zone_id: str | None = None
    is_active: bool = True
    source_reference: str | None = Field(None, max_length=500)
    validation_status: Literal["NOT_VALIDATED"] = "NOT_VALIDATED"


class SafetyRuleOut(SafetyRuleCreate):
    id: str
    validation_status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OperatingThresholdCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=80)
    metric: str = Field(..., min_length=1, max_length=80)
    value: float
    unit: str | None = Field(None, max_length=30)
    comparison: Comparison = "GT"
    zone_id: str | None = None
    camera_id: str | None = None
    threshold_source: ThresholdSource = "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
    source_reference: str | None = Field(None, max_length=500)
    validation_status: Literal["NOT_VALIDATED"] = "NOT_VALIDATED"

    @model_validator(mode="after")
    def validate_provenance(self):
        if not self.zone_id and not self.camera_id:
            raise ValueError("An operating threshold must be scoped to a zone or a camera")
        if self.threshold_source == "CONFIGURED" and not self.source_reference:
            raise ValueError("Configured thresholds require a source reference")
        return self


class OperatingThresholdOut(BaseModel):
    id: str
    code: str
    metric: str
    value: float
    unit: str | None = None
    comparison: str
    zone_id: str | None = None
    camera_id: str | None = None
    threshold_source: str
    source_reference: str | None = None
    validation_status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Escalation and notification policy
# ---------------------------------------------------------------------------

class EscalationPolicyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    event_type: str | None = Field(None, max_length=50)
    severity: str | None = Field(None, max_length=20)
    zone_id: str | None = None
    camera_id: str | None = None
    escalate_after_seconds: int = Field(900, ge=1, le=86400)
    from_role: str | None = Field(None, max_length=50)
    to_role: str = Field(..., min_length=2, max_length=50)
    escalation_level: int = Field(1, ge=1, le=10)
    notify_channels_json: list[str] | None = None
    is_active: bool = True
    source_reference: str | None = Field(None, max_length=500)


class EscalationPolicyOut(EscalationPolicyCreate):
    id: str
    created_by_user_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationPolicyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    event_type: str | None = Field(None, max_length=50)
    severity: str | None = Field(None, max_length=20)
    zone_id: str | None = None
    channel: str = Field(..., min_length=2, max_length=50)
    recipient_role: str = Field(..., min_length=2, max_length=50)
    dedup_window_seconds: int = Field(300, ge=0, le=86400)
    is_enabled: bool = True
    source_reference: str | None = Field(None, max_length=500)


class NotificationPolicyOut(NotificationPolicyCreate):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
