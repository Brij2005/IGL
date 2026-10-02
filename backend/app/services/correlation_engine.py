"""Event correlation.

Correlation groups repeated events of the same type from the same camera inside
a time window. It is derived entirely from events that already exist in the
database: it never creates an event, never re-scores one, and never reports a
count that was not counted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy.orm import Session

try:
    from app.models import Event, EventCorrelation
except ImportError:
    from backend.app.models import Event, EventCorrelation


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def correlation_key(camera_id: str | None, event_type: str, window_seconds: int) -> str:
    """Stable key for one correlation scope.

    The window is floored so that a policy change does not silently fork a
    single logical group into two.
    """
    scope = camera_id or "site-wide"
    return f"{scope}:{event_type}:{window_seconds}s"


def correlate_event(
    db: Session,
    event: Event,
    window_seconds: int = 300,
) -> EventCorrelation:
    """Fold one persisted event into its correlation group, creating it if needed."""
    if window_seconds < 1:
        raise ValueError("Correlation window must be at least one second")

    event_started_at = _as_utc(event.started_at) or datetime.now(timezone.utc)
    window_start = event_started_at - timedelta(seconds=window_seconds)
    window_end = event_started_at + timedelta(seconds=window_seconds)
    key = correlation_key(event.camera_id, event.event_type, window_seconds)

    group = (
        db.query(EventCorrelation)
        .filter(EventCorrelation.correlation_key == key)
        .order_by(EventCorrelation.last_event_at.desc())
        .first()
    )

    same_camera_type = [
        candidate
        for candidate in (
            db.query(Event)
            .filter(
                Event.event_type == event.event_type,
                Event.camera_id == event.camera_id,
                Event.started_at >= window_start,
                Event.started_at <= window_end,
            )
            .order_by(Event.started_at)
            .all()
        )
        if candidate.id is not None
    ]
    event_ids = [candidate.id for candidate in same_camera_type]
    counts = len(event_ids)

    if group is None:
        group = EventCorrelation(
            correlation_key=key,
            camera_id=event.camera_id,
            zone_id=event.zone_id,
            event_type=event.event_type,
            window_seconds=window_seconds,
            event_count=counts,
            event_ids_json=event_ids,
            first_event_at=same_camera_type[0].started_at if same_camera_type else event_started_at,
            last_event_at=same_camera_type[-1].started_at if same_camera_type else event_started_at,
        )
        db.add(group)
    else:
        group.event_count = counts
        group.event_ids_json = event_ids
        group.first_event_at = same_camera_type[0].started_at
        group.last_event_at = same_camera_type[-1].started_at
        group.zone_id = event.zone_id

    db.commit()
    db.refresh(group)
    return group


def correlation_summary(db: Session, limit: int = 100) -> List[dict]:
    """Recent correlation groups with their persisted event counts."""
    groups = (
        db.query(EventCorrelation)
        .order_by(EventCorrelation.last_event_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "correlation_key": group.correlation_key,
            "camera_id": group.camera_id,
            "zone_id": group.zone_id,
            "event_type": group.event_type,
            "window_seconds": group.window_seconds,
            "event_count": group.event_count,
            "first_event_at": group.first_event_at,
            "last_event_at": group.last_event_at,
        }
        for group in groups
    ]


def repeat_event_summary(db: Session, window_hours: int = 24, limit: int = 100) -> List[dict]:
    """Event types repeated most often in a window, counted from the database."""
    if window_hours < 1:
        raise ValueError("Summary window must be at least one hour")
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    rows = (
        db.query(Event.event_type, Event.camera_id)
        .filter(Event.started_at >= since)
        .all()
    )
    tallies: dict[tuple, int] = {}
    for event_type, camera_id in rows:
        tallies[(event_type, camera_id)] = tallies.get((event_type, camera_id), 0) + 1
    ordered = sorted(tallies.items(), key=lambda item: (-item[1], str(item[0])))[:limit]
    return [
        {"event_type": event_type, "camera_id": camera_id, "count": count}
        for (event_type, camera_id), count in ordered
    ]
