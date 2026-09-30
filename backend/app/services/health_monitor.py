"""
Camera Health Monitoring & Diagnostic Engine.
Evaluates stream telemetry, black frames, frozen frames, stream timeouts, and image quality.
CRITICAL SAFETY RULE: Camera/AI failure generates a CAMERA_FAILURE system event with NOT_ASSESSABLE state.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Tuple
import cv2
import numpy as np
from sqlalchemy.orm import Session

try:
    from app.models import Camera, CameraHealth, Event
    from app.services.video_ingestion import StreamReader, ingestion_manager
    from app.services.inference_pipeline import pipeline_manager
except ImportError:
    from backend.app.models import Camera, CameraHealth, Event
    from backend.app.services.video_ingestion import StreamReader, ingestion_manager
    from backend.app.services.inference_pipeline import pipeline_manager


class CameraHealthMonitor:
    """Diagnostic service monitoring industrial camera feed operational health."""

    def __init__(self):
        # Cache previous frame gray for frozen frame detection per camera
        self._prev_frames: Dict[str, np.ndarray] = {}
        self._frozen_counts: Dict[str, int] = {}

    def analyze_frame_quality(self, frame_bgr: np.ndarray) -> Tuple[bool, float]:
        """
        Analyze single frame image quality.
        Returns: (is_black: bool, sharpness_score: float)
        """
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        
        # Black frame detection (mean brightness < 10.0)
        mean_brightness = float(np.mean(gray))
        is_black = mean_brightness < 10.0

        # Image quality / sharpness score via Laplacian variance
        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        # Normalize score into range [0.0, 1.0] (capped at 500 variance)
        quality_score = min(round(laplacian_var / 500.0, 2), 1.0)

        return is_black, quality_score

    def check_frozen_frame(self, camera_id: str, frame_bgr: np.ndarray, threshold_mse: float = 0.5) -> bool:
        """
        Detect frozen frames by comparing Mean Squared Error (MSE) between consecutive frames.
        Returns True if consecutive frames remain unchanged.
        """
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        prev_gray = self._prev_frames.get(camera_id)

        if prev_gray is None or prev_gray.shape != gray.shape:
            self._prev_frames[camera_id] = gray
            self._frozen_counts[camera_id] = 0
            return False

        mse = float(np.mean((gray.astype(float) - prev_gray.astype(float)) ** 2))
        self._prev_frames[camera_id] = gray

        if mse < threshold_mse:
            self._frozen_counts[camera_id] = self._frozen_counts.get(camera_id, 0) + 1
        else:
            self._frozen_counts[camera_id] = 0

        # Flag as frozen if 5 consecutive frame checks show zero motion
        return self._frozen_counts.get(camera_id, 0) >= 5

    def evaluate_camera_health(
        self,
        db: Session,
        camera: Camera,
        reader: Optional[StreamReader] = None
    ) -> CameraHealth:
        """
        Perform a full health diagnostic check on a camera feed.
        Updates CameraHealth in DB and dispatches CAMERA_FAILURE safety event if degraded/offline.
        """
        if reader is None:
            reader = ingestion_manager.get_reader(camera.id)

        now = datetime.now(timezone.utc)
        health = db.query(CameraHealth).filter(CameraHealth.camera_id == camera.id).first()
        if not health:
            health = CameraHealth(camera_id=camera.id)
            db.add(health)

        if not camera.is_active:
            health.status = "OFFLINE"
            health.inference_status = "NOT_RUNNING"
            health.health_timestamp = now
            db.commit()
            db.refresh(health)
            return health

        # Check stream worker status
        if reader is None or not reader.is_connected:
            health.status = "OFFLINE"
            health.fps = 0.0
            health.inference_status = "UNAVAILABLE"
            health.health_timestamp = now
            
            # Generate CAMERA_FAILURE safety event
            self._dispatch_camera_failure_event(
                db=db,
                camera=camera,
                reason="Camera feed disconnected or offline",
                severity="HIGH"
            )
            
            db.commit()
            db.refresh(health)
            return health

        # Stream is connected: evaluate latest frame
        latest = reader.get_latest_frame()
        if latest is None:
            health.status = "DEGRADED"
            health.fps = reader.current_fps
            health.health_timestamp = now
            db.commit()
            db.refresh(health)
            return health

        frame_ts, frame = latest
        
        # Check stream timeout (> 5.0 seconds since last frame)
        time_since_frame = (now - frame_ts).total_seconds()
        if time_since_frame > 5.0:
            health.status = "OFFLINE"
            health.health_timestamp = now
            self._dispatch_camera_failure_event(
                db=db,
                camera=camera,
                reason=f"Stream timeout: No frame received for {time_since_frame:.1f}s",
                severity="HIGH"
            )
            db.commit()
            db.refresh(health)
            return health

        # Run image diagnostic checks
        is_black, quality_score = self.analyze_frame_quality(frame)
        is_frozen = self.check_frozen_frame(camera.id, frame)

        health.last_frame_timestamp = frame_ts
        health.fps = reader.current_fps
        health.is_black = is_black
        health.is_frozen = is_frozen
        health.image_quality_score = quality_score
        health.health_timestamp = now
        pipeline_states = pipeline_manager.pipeline_status(camera.id)
        health.inference_status = pipeline_states[0]["inference_status"] if pipeline_states else "NOT_RUNNING"

        if is_black or is_frozen:
            health.status = "UNRELIABLE"
            reason = "Black frame detected" if is_black else "Frozen video frame detected"
            self._dispatch_camera_failure_event(
                db=db,
                camera=camera,
                reason=reason,
                severity="MEDIUM"
            )
        else:
            health.status = "ONLINE"

        db.commit()
        db.refresh(health)
        return health

    def _dispatch_camera_failure_event(
        self,
        db: Session,
        camera: Camera,
        reason: str,
        severity: str = "HIGH"
    ) -> Optional[Event]:
        """
        SAFETY SIGNAL RULE: Generate a CAMERA_FAILURE safety event with NOT_ASSESSABLE state.
        Deduplicates active open failure events for the same camera within 60 seconds.
        """
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(seconds=60)
        
        existing_event = db.query(Event).filter(
            Event.camera_id == camera.id,
            Event.event_type == "CAMERA_FAILURE",
            Event.started_at >= recent_cutoff
        ).first()

        if existing_event:
            return existing_event

        event = Event(
            camera_id=camera.id,
            zone_id=camera.zone_id,
            event_type="CAMERA_FAILURE",
            observation_state="NOT_ASSESSABLE",  # Camera failure means safety state is NOT_ASSESSABLE
            severity=severity,
            workflow_state="NEW",
            confidence=1.0,
            started_at=now,
            model_version="SYSTEM_HEALTH_V1"
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event


# Global health monitor instance
health_monitor = CameraHealthMonitor()
