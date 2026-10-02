"""Read-only access to persisted visual tracking sessions."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

try:
    from app.access_control import require_permission
    from app.database import get_db
    from app.models import Camera, Detection, Event, Track, User
    from app.schemas_workers import WorkerTrackOut
except ImportError:
    from backend.app.access_control import require_permission
    from backend.app.database import get_db
    from backend.app.models import Camera, Detection, Event, Track, User
    from backend.app.schemas_workers import WorkerTrackOut


router = APIRouter()


@router.get("/tracks", response_model=list[WorkerTrackOut])
def list_worker_tracks(
    recent_only: bool = False,
    recent_within_seconds: int = Query(default=30, ge=1, le=3600),
    camera_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: User | None = Depends(require_permission("cameras:view")),
):
    """Return real stored visual tracks without linking them to staff identities.

    Recentness is a UI freshness signal derived from the persisted last-seen
    timestamp; it does not mean the person remains visible now.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=recent_within_seconds)
    query = db.query(Track).join(Camera, Track.camera_id == Camera.id)
    if camera_id:
        query = query.filter(Track.camera_id == camera_id)
    if recent_only:
        query = query.filter(Track.last_seen_at >= cutoff)
    tracks = query.order_by(Track.last_seen_at.desc(), Track.id).offset(offset).limit(limit).all()
    if not tracks:
        return []

    track_ids = [track.id for track in tracks]
    camera_ids = {track.camera_id for track in tracks}
    cameras = {item.id: item for item in db.query(Camera).filter(Camera.id.in_(camera_ids)).all()}
    latest_detection_times = (
        db.query(Detection.track_id.label("track_id"), func.max(Detection.timestamp).label("timestamp"))
        .filter(Detection.track_id.in_(track_ids))
        .group_by(Detection.track_id)
        .subquery()
    )
    detections = (
        db.query(Detection)
        .join(latest_detection_times, (Detection.track_id == latest_detection_times.c.track_id) & (Detection.timestamp == latest_detection_times.c.timestamp))
        .order_by(Detection.id)
        .all()
    )
    latest_detection: dict[str, Detection] = {}
    for detection in detections:
        latest_detection.setdefault(detection.track_id, detection)

    events = (
        db.query(Event)
        .filter(Event.track_id.in_(track_ids), Event.workflow_state.notin_(["RESOLVED", "CLOSED"]))
        .order_by(Event.started_at.desc())
        .limit(2000)
        .all()
    )
    active_events: dict[str, list[Event]] = {}
    for event in events:
        active_events.setdefault(event.track_id, []).append(event)

    result = []
    for track in tracks:
        camera = cameras[track.camera_id]
        detection = latest_detection.get(track.id)
        last_seen = track.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        result.append(WorkerTrackOut(
            track_id=track.track_uuid,
            object_class=track.object_class,
            camera_id=camera.id,
            camera_name=camera.name,
            zone_id=camera.zone_id,
            zone_name=camera.zone.name if camera.zone else None,
            first_seen_at=track.first_seen_at,
            last_seen_at=track.last_seen_at,
            freshness="RECENTLY_OBSERVED" if last_seen >= cutoff else "STALE",
            latest_detection={
                "object_class": detection.object_class,
                "confidence": detection.confidence,
                "timestamp": detection.timestamp,
                "bbox": detection.bbox_json,
                "observation_state": detection.observation_state,
            } if detection else None,
            active_events=[{
                "id": event.id,
                "event_type": event.event_type,
                "severity": event.severity,
                "observation_state": event.observation_state,
                "workflow_state": event.workflow_state,
                "started_at": event.started_at,
            } for event in active_events.get(track.id, [])],
        ))
    return result
