"""
Pydantic schemas for Camera Management and Real-time Telemetry.
"""
from datetime import datetime
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator

try:
    from app.utils.redaction import display_source_identifier
except ImportError:
    from backend.app.utils.redaction import display_source_identifier


# The database column is 1200 characters wide; keep the API limit aligned.
MAX_STREAM_URL_LENGTH = 1200


def sanitize_stream_url(stream_url: str) -> str:
    """Return a display-safe source identifier.

    Delegates to the shared redaction helper so API responses, validation
    reports, and logs cannot drift apart. User information, query parameters,
    and fragments are always removed, and a source that cannot be parsed is
    reduced to its final path segment rather than echoed.
    """
    return display_source_identifier(stream_url)


class CameraBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    code: str = Field(..., min_length=2, max_length=50)
    stream_url: str = Field(..., min_length=5, max_length=MAX_STREAM_URL_LENGTH)
    camera_type: str = Field("RTSP", max_length=50)  # RTSP, IP, PTZ, FIXED, USB, FILE
    fps: float = Field(25.0, ge=1.0, le=120.0)
    resolution: str = Field("1920x1080", max_length=20)
    location_description: Optional[str] = None
    zone_id: Optional[str] = None


class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    stream_url: Optional[str] = Field(None, min_length=5, max_length=MAX_STREAM_URL_LENGTH)
    camera_type: Optional[str] = Field(None, max_length=50)
    fps: Optional[float] = Field(None, ge=1.0, le=120.0)
    resolution: Optional[str] = Field(None, max_length=20)
    location_description: Optional[str] = None
    zone_id: Optional[str] = None
    is_active: Optional[bool] = None


class CameraHealthOut(BaseModel):
    """Persisted camera-health row as written by the health worker."""

    id: str
    camera_id: str
    status: str
    last_frame_timestamp: Optional[datetime] = None
    measured_fps: Optional[float] = None
    frame_latency_ms: Optional[float] = None
    observed_resolution: Optional[str] = None
    dropped_frames: Optional[int] = None
    brightness_score: Optional[float] = None
    sharpness_score: Optional[float] = None
    image_quality_score: Optional[float] = None
    is_black: Optional[bool] = None
    is_frozen: Optional[bool] = None
    inference_status: str
    health_timestamp: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CameraHealthSnapshotOut(BaseModel):
    """Read-only health view.

    A field is a recorded observation, an explicit "not observed" state, or
    null. Nothing here is computed on the read path, so a snapshot can never
    report a status that was not actually observed.
    """

    camera_id: str
    status: str
    inference_status: str
    observation_source: str
    observation_stale: bool = False
    is_active: bool = True
    reader_status: str = "NOT_STARTED"
    pipeline_states: list[dict] = Field(default_factory=list)
    health_timestamp: Optional[datetime] = None
    last_frame_timestamp: Optional[datetime] = None
    measured_fps: Optional[float] = None
    frame_latency_ms: Optional[float] = None
    observed_resolution: Optional[str] = None
    dropped_frames: Optional[int] = None
    brightness_score: Optional[float] = None
    sharpness_score: Optional[float] = None
    image_quality_score: Optional[float] = None
    is_black: Optional[bool] = None
    is_frozen: Optional[bool] = None


class CameraOut(CameraBase):
    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    health: Optional[CameraHealthOut] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("stream_url")
    @classmethod
    def sanitize_stream_url_field(cls, value: str) -> str:
        return sanitize_stream_url(value)
