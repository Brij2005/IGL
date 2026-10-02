"""Event read and validated workflow transition schemas.

The read schema is the event's provenance: what was observed, which model and
weights produced it, which detector rule and zone it came from, the temporal
verdict, the tracked object, and which incidents were raised against it. Every
field is either read from the persisted event or derived from rows that link to
it; none of them is filled in with a default that would imply evidence the
platform does not have.
"""
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

    # Provenance. Every one of these is NULL when the corresponding evidence was
    # never recorded, which is why they are nullable rather than defaulted.
    zone_code: str | None = None
    track_uuid: str | None = None
    detector_key: str | None = None
    verification_state: str | None = None
    temporal_observations: int | None = None
    temporal_duration_seconds: float | None = None
    threshold_source: str | None = None
    source_reference: str | None = None
    model_name: str | None = None
    model_weights_checksum: str | None = None
    # The frame timestamp of the observation that last updated this event. It
    # falls back to started_at for events written before the frame timestamp was
    # recorded in provenance.
    frame_timestamp: datetime | None = None
    evidence_count: int = 0
    correlation_keys: list[str] = Field(default_factory=list)
    # Incidents raised against this event, derived from the existing incident
    # rows that point at it. An empty list means no incident has been raised,
    # which is different from the linkage being unavailable.
    incident_ids: list[str] = Field(default_factory=list)
    allowed_transitions: list[str] = Field(default_factory=list)
    provenance: dict | None = None

    model_config = ConfigDict(from_attributes=True)
