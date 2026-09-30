"""
Pydantic schemas for Camera Management and Real-time Telemetry.
"""
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pydantic import BaseModel, Field, ConfigDict, field_validator


_CREDENTIAL_QUERY_KEYS = {"auth", "key", "pass", "password", "pwd", "secret", "token", "user", "username"}


def sanitize_stream_url(stream_url: str) -> str:
    """Remove URL userinfo and redact credential-like query parameters."""
    try:
        parsed = urlsplit(stream_url)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        if parsed.port is not None:
            hostname = f"{hostname}:{parsed.port}"
        query = [
            (key, "REDACTED" if key.lower() in _CREDENTIAL_QUERY_KEYS else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunsplit((parsed.scheme, hostname, parsed.path, urlencode(query), ""))
    except ValueError:
        return "CONFIGURATION_REQUIRED"


class CameraBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    code: str = Field(..., min_length=2, max_length=50)
    stream_url: str = Field(..., min_length=5, max_length=500)
    camera_type: str = Field("RTSP", max_length=50)  # RTSP, IP, PTZ, FIXED, USB, FILE
    fps: float = Field(25.0, ge=1.0, le=120.0)
    resolution: str = Field("1920x1080", max_length=20)
    location_description: Optional[str] = None
    zone_id: Optional[str] = None


class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    stream_url: Optional[str] = Field(None, min_length=5, max_length=500)
    camera_type: Optional[str] = Field(None, max_length=50)
    fps: Optional[float] = Field(None, ge=1.0, le=120.0)
    resolution: Optional[str] = Field(None, max_length=20)
    location_description: Optional[str] = None
    zone_id: Optional[str] = None
    is_active: Optional[bool] = None


class CameraHealthOut(BaseModel):
    id: str
    camera_id: str
    status: str  # ONLINE, DEGRADED, OFFLINE, UNRELIABLE, UNKNOWN
    last_frame_timestamp: Optional[datetime] = None
    fps: float
    latency_ms: float
    is_frozen: bool
    is_black: bool
    image_quality_score: float
    inference_status: str
    health_timestamp: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


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
