"""Response schemas for endpoints that previously returned untyped payloads."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class EvidenceOut(BaseModel):
    id: str
    event_id: str
    evidence_type: str
    file_hash: str
    metadata: Optional[Any] = None
    created_at: datetime
    status: str

    model_config = ConfigDict(from_attributes=True)


class AnalyticsSummaryOut(BaseModel):
    data_status: str
    plant_count: int
    camera_count: int
    online_camera_count: int
    event_count: int
    verified_event_count: int
    incident_count: int
    near_miss_count: int
    corrective_action_count: int
    accuracy_metrics_status: str
    igl_validated: bool


class NotificationOut(BaseModel):
    id: str
    event_id: Optional[str] = None
    user_id: Optional[str] = None
    recipient_role: Optional[str] = None
    channel: str
    recipient: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    retry_count: int = 0
    max_attempts: int = 0
    sent_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None
    next_attempt_at: Optional[datetime] = None
    provider: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class NotificationChannelStatusOut(BaseModel):
    """Per-channel delivery state.

    ``configuration_state`` is NOT_CONFIGURED whenever this build has no
    transport for the channel, so a queue depth can never be mistaken for
    evidence that an alert was delivered.
    """
    channel: str
    configuration_state: str
    queued: int = 0
    sent: int = 0
    failed: int = 0
    not_configured: int = 0


class ModelHealthOut(BaseModel):
    status: str
    available: bool
    weights_loaded: bool = False
    weights_checksum_sha256: Optional[str] = None
    model_name: str
    model_version: str
    classes: list[str] = Field(default_factory=list)
    confidence_threshold: float
    device: str
    inference_latency_ms: Optional[float] = None
    average_inference_latency_ms: Optional[float] = None
    inference_count: int
    inference_attempt_count: int = 0
    inference_failure_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class PipelineStatusOut(BaseModel):
    camera_id: str
    pipeline_state: str
    inference_status: str
    stream_status: str
    frames_seen: int
    inferences_attempted: int
    inferences_completed: int
    inference_failures: int
    tracking_failures: int
    detection_count: int
    last_frame_timestamp: Optional[datetime] = None
    last_inference_timestamp: Optional[datetime] = None
    last_error_type: Optional[str] = None
    buffer_frames: int
    buffer_memory_bytes: int

    model_config = ConfigDict(from_attributes=True)


class AIHealthOut(BaseModel):
    model: ModelHealthOut
    pipelines: list[PipelineStatusOut]
    igl_validation_status: str
