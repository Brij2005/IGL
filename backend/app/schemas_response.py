"""Schemas for the safety response lifecycle: incidents, near-misses, and actions.

None of these models carry a measured value. A record is created only from an
already-persisted event and an authenticated operator, so every field here is
either operator-supplied or read back from the database.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

try:
    from app.services.response_engine import (
        CORRECTIVE_ACTION_STATES,
        INCIDENT_STATES,
        NEAR_MISS_STATES,
    )
except ImportError:
    from backend.app.services.response_engine import (
        CORRECTIVE_ACTION_STATES,
        INCIDENT_STATES,
        NEAR_MISS_STATES,
    )


Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class TransitionRequest(BaseModel):
    """A state change request. The reason is mandatory and is stored."""

    new_state: str = Field(..., min_length=2, max_length=50)
    reason: str = Field(..., min_length=1, max_length=2000)


class StateTransitionOut(BaseModel):
    previous_state: str
    new_state: str
    reason: str
    user_id: Optional[str] = None
    transitioned_at: datetime


class IncidentCreate(BaseModel):
    """An operator-raised incident. Requires a persisted event to exist."""

    event_id: str = Field(..., min_length=1, max_length=36)
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    severity: Severity = "HIGH"


class IncidentOut(BaseModel):
    id: str
    event_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    severity: str
    status: str
    allowed_transitions: list[str] = Field(default_factory=list)
    reported_by_user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    corrective_action_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class NearMissCreate(BaseModel):
    """An operator-raised near-miss. Requires a persisted event to exist."""

    event_id: str = Field(..., min_length=1, max_length=36)
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    potential_severity: Severity = "HIGH"
    interaction_type: Optional[str] = Field(None, max_length=100)


class NearMissOut(BaseModel):
    id: str
    event_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    potential_severity: str
    interaction_type: Optional[str] = None
    status: str
    allowed_transitions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    corrective_action_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class CorrectiveActionCreate(BaseModel):
    """A corrective action against an event, incident, or near-miss.

    Exactly one parent must be supplied, so an action always belongs to a
    recorded observation and can never dangle.
    """

    action_description: str = Field(..., min_length=5, max_length=5000)
    event_id: Optional[str] = Field(None, max_length=36)
    incident_id: Optional[str] = Field(None, max_length=36)
    near_miss_id: Optional[str] = Field(None, max_length=36)
    assigned_to_user_id: Optional[str] = Field(None, max_length=36)
    due_date: Optional[datetime] = None

    def validated_parent(self) -> tuple[str, str]:
        parents = {
            "event_id": self.event_id,
            "incident_id": self.incident_id,
            "near_miss_id": self.near_miss_id,
        }
        supplied = [name for name, value in parents.items() if value]
        if len(supplied) != 1:
            raise ValueError(
                "Exactly one of event_id, incident_id, or near_miss_id must be supplied"
            )
        return supplied[0], parents[supplied[0]]


class CorrectiveActionOut(BaseModel):
    id: str
    event_id: Optional[str] = None
    incident_id: Optional[str] = None
    near_miss_id: Optional[str] = None
    action_description: str
    assigned_to_user_id: Optional[str] = None
    status: str
    allowed_transitions: list[str] = Field(default_factory=list)
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AcknowledgementCreate(BaseModel):
    notes: Optional[str] = Field(None, max_length=2000)


class AcknowledgementOut(BaseModel):
    id: str
    event_id: str
    # Nullable because nothing authenticates a request in this build, so the
    # acknowledging identity is usually unknown. A null value means the
    # platform could not prove who handled the event.
    user_id: Optional[str] = None
    acknowledged_at: datetime
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AssignmentCreate(BaseModel):
    assigned_to_user_id: str = Field(..., min_length=1, max_length=36)
    due_at: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=2000)


class AssignmentOut(BaseModel):
    id: str
    event_id: str
    assigned_to_user_id: str
    assigned_by_user_id: Optional[str] = None
    assigned_at: datetime
    due_at: Optional[datetime] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ResponseLifecycleStatesOut(BaseModel):
    """The declared state machines, so a client never has to guess them."""

    incident: list[str]
    near_miss: list[str]
    corrective_action: list[str]
    event: list[str]
