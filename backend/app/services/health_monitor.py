"""Camera Health Monitoring & Diagnostic Engine.

Two operations exist and they are deliberately different:

* :func:`read_camera_health` returns the last *persisted* observation. It never
  touches the database, never opens a stream, and never creates an event. This is
  what a read API must call.
* :meth:`CameraHealthMonitor.evaluate_camera_health` observes real frames,
  persists the result, and may dispatch a ``CAMERA_FAILURE`` event. It is called
  by the background health worker and by the explicit refresh endpoint, never by
  a plain read.

Only observed evidence can produce ``ONLINE``. A camera with no observed frame
is reported as configured or connecting, with every measured value ``NULL``.

A camera failure is an observation about camera availability, not a safety
detection: it is written with ``observation_state=NOT_ASSESSABLE`` and no
confidence or duration, because no model scored it.
"""
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import logging

import cv2
import numpy as np
from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.models import Camera, CameraHealth, Event
    from app.services.video_ingestion import StreamReader, ingestion_manager
    from app.services.inference_pipeline import pipeline_manager
except ImportError:
    from backend.app.config import settings
    from backend.app.models import Camera, CameraHealth, Event
    from backend.app.services.video_ingestion import StreamReader, ingestion_manager
    from backend.app.services.inference_pipeline import pipeline_manager


logger = logging.getLogger("igl.camera.health_monitor")

#: Statuses that mean "no usable frames have been observed".
UNOBSERVED_STATUSES = ("CONFIGURED", "CONNECTING")

#: Cooldown during which a repeated failure for the same camera is suppressed.
FAILURE_EVENT_COOLDOWN_SECONDS = 60

#: Frame-identity comparisons used by the frozen-source check.
FROZEN_COMPARISON_FRAMES = 3


def read_camera_health(db: Session, camera: Camera) -> dict:
    """Return the last persisted health observation for ``camera``.

    Read-only: no evaluation, no write, no event. When nothing has been observed
    yet the response states that explicitly instead of inventing a status.
    """
    health = db.query(CameraHealth).filter(CameraHealth.camera_id == camera.id).first()
    if health is None:
        return {
            "camera_id": camera.id,
            "status": "NEVER_EVALUATED",
            "inference_status": "NOT_RUNNING",
            "observation_source": "NO_RECORDED_OBSERVATION",
            "health_timestamp": None,
            "last_frame_timestamp": None,
            "measured_fps": None,
            "frame_latency_ms": None,
            "observed_resolution": None,
            "dropped_frames": None,
            "brightness_score": None,
            "sharpness_score": None,
            "image_quality_score": None,
            "is_black": None,
            "is_frozen": None,
        }
    return {
        "camera_id": health.camera_id,
        "status": health.status,
        "inference_status": health.inference_status,
        "observation_source": "RECORDED_OBSERVATION",
        "health_timestamp": health.health_timestamp,
        "last_frame_timestamp": health.last_frame_timestamp,
        "measured_fps": health.measured_fps,
        "frame_latency_ms": health.frame_latency_ms,
        "observed_resolution": health.observed_resolution,
        "dropped_frames": health.dropped_frames,
        "brightness_score": health.brightness_score,
        "sharpness_score": health.sharpness_score,
        "image_quality_score": health.image_quality_score,
        "is_black": health.is_black,
        "is_frozen": health.is_frozen,
    }


def stale_health(health: dict, reference: datetime | None = None) -> bool:
    """Return True when the recorded observation is older than it may be trusted."""
    observed_at = health.get("health_timestamp")
    if observed_at is None:
        return True
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    now = reference or datetime.now(timezone.utc)
    return (now - observed_at).total_seconds() > max(settings.CAMERA_HEALTH_INTERVAL_SECONDS * 3, 30.0)


class CameraHealthMonitor:
    """Diagnostic service monitoring industrial camera feed operational health."""

    def analyze_frame_quality(self, frame_bgr: np.ndarray) -> Tuple[bool, float]:
        """Return ``(is_black, sharpness_score)`` for one observed frame.

        Thresholds are configurable and are engineering defaults, not calibrated
        IGL values.
        """
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        mean_brightness = float(np.mean(gray))
        is_black = mean_brightness < settings.CAMERA_BLACK_MEAN_BRIGHTNESS
        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        quality_score = min(
            round(laplacian_var / max(settings.CAMERA_SHARPNESS_REFERENCE_VARIANCE, 1e-6), 2),
            1.0,
        )
        return is_black, quality_score

    def is_source_frozen(self, frames: List[np.ndarray]) -> Tuple[bool, str | None]:
        """Decide whether a source is stalled using distinct observed frames.

        A single repeated frame proves nothing: a night-time camera pointed at a
        static scene legitimately produces near-identical images while the source
        is healthy. The check therefore compares at least
        ``CAMERA_FROZEN_DISTINCT_FRAMES`` distinct buffered frames, requires
        their timestamps to have advanced, and only then reports a frozen source.
        Fewer frames than the configured minimum yields ``False`` with a reason
        instead of a verdict.
        """
        required = max(settings.CAMERA_FROZEN_DISTINCT_FRAMES, 2)
        if len(frames) < required:
            return False, f"INSUFFICIENT_OBSERVED_FRAMES ({len(frames)}/{required})"
        comparable = frames[-FROZEN_COMPARISON_FRAMES:] if len(frames) >= FROZEN_COMPARISON_FRAMES else frames
        if len(comparable) < 2:
            return False, "INSUFFICIENT_OBSERVED_FRAMES"
        gray = [cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) for frame in comparable]
        shapes = {item.shape for item in gray}
        if len(shapes) > 1:
            return False, "FRAME_GEOMETRY_CHANGED"
        deltas = [
            float(np.mean((later.astype(float) - earlier.astype(float)) ** 2))
            for earlier, later in zip(gray, gray[1:])
        ]
        if all(delta < settings.CAMERA_FROZEN_MSE_THRESHOLD for delta in deltas):
            return True, f"FROZEN_ACROSS_{len(comparable)}_DISTINCT_FRAMES"
        return False, None

    def evaluate_camera_health(
        self,
        db: Session,
        camera: Camera,
        reader: Optional[StreamReader] = None,
        *,
        dispatch_events: bool = True,
    ) -> CameraHealth:
        """Observe the camera, persist the result, and record a failure event.

        This is the only path that writes camera health. ``dispatch_events`` is
        disabled by tests that must not create events.
        """
        if reader is None:
            reader = ingestion_manager.get_reader(camera.id)

        now = datetime.now(timezone.utc)
        health = db.query(CameraHealth).filter(CameraHealth.camera_id == camera.id).first()
        if health is None:
            health = CameraHealth(camera_id=camera.id, status="CONFIGURED")
            db.add(health)
            db.flush()

        if not camera.is_active:
            self._record_unobserved(health, now, status="OFFLINE", reason="Camera is deactivated")
            if dispatch_events:
                self._dispatch_camera_failure_event(
                    db=db, camera=camera, reason="Camera is deactivated", severity="LOW"
                )
            db.commit()
            db.refresh(health)
            return health

        if reader is None or not reader.is_connected:
            status = "CONNECTING" if reader is not None and reader.status == "STARTING" else "OFFLINE"
            reason = "Camera source is not connected"
            self._record_unobserved(health, now, status=status, reason=reason)
            if dispatch_events:
                self._dispatch_camera_failure_event(db=db, camera=camera, reason=reason, severity="HIGH")
            db.commit()
            db.refresh(health)
            return health

        buffered = reader.get_frame_buffer()
        if not buffered:
            self._record_unobserved(health, now, status="CONNECTING", reason="No frame buffered yet")
            db.commit()
            db.refresh(health)
            return health

        frame_timestamp, latest_frame = buffered[-1]
        time_since_frame = (now - frame_timestamp).total_seconds()
        if time_since_frame > settings.CAMERA_STALE_FRAME_SECONDS:
            reason = (
                f"Stream stale: no frame for {time_since_frame:.1f}s "
                f"(threshold {settings.CAMERA_STALE_FRAME_SECONDS:.1f}s)"
            )
            self._record_unobserved(health, now, status="STALE", reason=reason)
            if dispatch_events:
                self._dispatch_camera_failure_event(db=db, camera=camera, reason=reason, severity="HIGH")
            db.commit()
            db.refresh(health)
            return health

        is_black, quality_score = self.analyze_frame_quality(latest_frame)
        distinct = self._distinct_frames(buffered)
        is_frozen, frozen_reason = self.is_source_frozen([item[1] for item in distinct])

        health.last_frame_timestamp = frame_timestamp
        health.measured_fps = reader.current_fps if reader.current_fps > 0 else None
        health.frame_latency_ms = max(time_since_frame * 1000.0, 0.0)
        health.observed_resolution = f"{latest_frame.shape[1]}x{latest_frame.shape[0]}"
        health.dropped_frames = reader.dropped_frames_count
        health.brightness_score = float(np.mean(cv2.cvtColor(latest_frame, cv2.COLOR_BGR2GRAY)) / 255.0)
        health.sharpness_score = quality_score
        health.image_quality_score = quality_score
        health.is_black = is_black
        health.is_frozen = is_frozen
        health.health_timestamp = now
        pipeline_states = pipeline_manager.pipeline_status(camera.id)
        health.inference_status = (
            pipeline_states[0]["inference_status"] if pipeline_states else "NOT_RUNNING"
        )

        if is_black:
            health.status = "BLACK_FRAME"
            reason = f"Black frame detected: mean brightness below {settings.CAMERA_BLACK_MEAN_BRIGHTNESS:.1f}"
            severity = "MEDIUM"
        elif is_frozen:
            health.status = "FROZEN"
            reason = f"Frozen source: {frozen_reason}"
            severity = "MEDIUM"
        else:
            health.status = "ONLINE"
            reason = None
            severity = "MEDIUM"

        if reason and dispatch_events:
            self._dispatch_camera_failure_event(db=db, camera=camera, reason=reason, severity=severity)

        db.commit()
        db.refresh(health)
        return health

    @staticmethod
    def _distinct_frames(buffered: List[Tuple[datetime, np.ndarray]]) -> List[Tuple[datetime, np.ndarray]]:
        """Return up to the configured number of the most recent distinct frames.

        Duplicate timestamps are collapsed so a reader that re-adds the same
        frame cannot make a healthy source look frozen.
        """
        required = max(settings.CAMERA_FROZEN_DISTINCT_FRAMES, 2)
        distinct: List[Tuple[datetime, np.ndarray]] = []
        seen_timestamps = set()
        for timestamp, frame in reversed(buffered):
            if timestamp in seen_timestamps:
                continue
            seen_timestamps.add(timestamp)
            distinct.append((timestamp, frame))
            if len(distinct) >= required:
                break
        distinct.reverse()
        return distinct

    def _record_unobserved(self, health: CameraHealth, now: datetime, *, status: str, reason: str) -> None:
        """Persist an explicit non-ONLINE state with every measured value NULL."""
        logger.info(
            "Camera observation unavailable",
            extra={
                "component": "camera_health",
                "camera_id": health.camera_id,
                "status": status,
                "reason": reason,
            },
        )
        health.status = status
        health.inference_status = "NOT_RUNNING"
        health.health_timestamp = now
        health.measured_fps = None
        health.frame_latency_ms = None
        health.observed_resolution = None
        health.dropped_frames = None
        health.brightness_score = None
        health.sharpness_score = None
        health.image_quality_score = None
        health.is_black = None
        health.is_frozen = None
        health.last_frame_timestamp = None

    def _dispatch_camera_failure_event(
        self,
        db: Session,
        camera: Camera,
        reason: str,
        severity: str = "HIGH",
    ) -> Optional[Event]:
        """Record an observed camera failure, deduplicated per camera.

        The event states that the safety condition is not assessable and carries
        no confidence, because no model produced it.
        """
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(seconds=FAILURE_EVENT_COOLDOWN_SECONDS)
        existing_event = db.query(Event).filter(
            Event.camera_id == camera.id,
            Event.event_type == "CAMERA_FAILURE",
            Event.started_at >= recent_cutoff,
        ).first()
        if existing_event is not None:
            return existing_event

        event = Event(
            camera_id=camera.id,
            zone_id=camera.zone_id,
            event_type="CAMERA_FAILURE",
            observation_state="NOT_ASSESSABLE",
            severity=severity,
            workflow_state="NEW",
            confidence=None,
            duration_seconds=None,
            started_at=now,
            model_version="SYSTEM_HEALTH_V1",
        )
        db.add(event)
        db.flush()
        logger.warning(
            "Camera failure event recorded",
            extra={
                "component": "camera_health",
                "camera_id": camera.id,
                "event_id": event.id,
                "severity": severity,
            },
        )
        return event


#: Global health monitor instance.
health_monitor = CameraHealthMonitor()
