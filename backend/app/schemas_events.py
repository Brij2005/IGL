"""Event read and validated workflow transition schemas."""
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class EventWorkflowTransitionRequest(BaseModel):
    new_state: str = Field(..., min_length=2, max_length=50)
    reason: str = Field(..., min_length=1, max_length=2000)


class EventOut(BaseModel):
    id: str
    camera_id: str
    zone_id: str | None
    track_id: str | None
    event_type: str
    observation_state: str
    severity: str
    workflow_state: str
    # Absent when the observation is NOT_ASSESSABLE, for example a camera
    # failure. There is no measured value to report in that case, so the field
    # stays null rather than carrying a fabricated number.
    confidence: float | None
    duration_seconds: float | None
    started_at: datetime
    ended_at: datetime | None
    model_version: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)