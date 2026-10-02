"""Read-only visual track records. Track IDs are not employee identities."""
from datetime import datetime

from pydantic import BaseModel


class WorkerTrackEventOut(BaseModel):
    id: str
    event_type: str
    severity: str
    observation_state: str
    workflow_state: str
    started_at: datetime


class WorkerTrackDetectionOut(BaseModel):
    object_class: str
    confidence: float
    timestamp: datetime
    bbox: list[float]
    observation_state: str


class WorkerTrackOut(BaseModel):
    track_id: str
    object_class: str
    camera_id: str
    camera_name: str
    zone_id: str | None
    zone_name: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    freshness: str
    latest_detection: WorkerTrackDetectionOut | None
    active_events: list[WorkerTrackEventOut]
    helmet_status: str = "UNKNOWN"
    ppe_status: str = "UNKNOWN"
    phone_status: str = "UNKNOWN"

