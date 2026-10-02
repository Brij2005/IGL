"""Lifecycle-managed polling for configured camera pipelines and health."""
from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.database import SessionLocal
    from app.models import Camera
    from app.services.health_monitor import health_monitor
    from app.services.inference_pipeline import pipeline_manager
    from app.services.video_ingestion import ingestion_manager
    from app.services.webcam_source import is_webcam_source
except ImportError:
    from backend.app.config import settings
    from backend.app.database import SessionLocal
    from backend.app.models import Camera
    from backend.app.services.health_monitor import health_monitor
    from backend.app.services.inference_pipeline import pipeline_manager
    from backend.app.services.video_ingestion import ingestion_manager
    from backend.app.services.webcam_source import is_webcam_source


logger = logging.getLogger("igl.camera.health_worker")


class ContinuousCameraHealthWorker:
    def __init__(self, session_factory=SessionLocal, interval_seconds: float | None = None):
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds or settings.CAMERA_HEALTH_INTERVAL_SECONDS
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self.status = "STOPPED"
        self.last_error_type: str | None = None

    def start(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self.status = "RUNNING"
            self._thread = threading.Thread(target=self._run, name="camera-health", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        with self._state_lock:
            self.status = "STOPPED" if not thread or not thread.is_alive() else "STOP_TIMEOUT"

    def poll_once(self, db: Session | None = None) -> None:
        owns_session = db is None
        session = db or self.session_factory()
        try:
            cameras = session.query(Camera).filter(Camera.is_active.is_(True)).all()
            for camera in cameras:
                try:
                    reader = ingestion_manager.get_reader(camera.id)
                    webcam_not_started = is_webcam_source(camera.stream_url) and reader is None
                    if pipeline_manager.is_operator_stopped(camera.id) or webcam_not_started:
                        health_monitor.evaluate_camera_health(session, camera, dispatch_events=False)
                        continue
                    pipeline_manager.start_stream(camera.id, camera.stream_url, camera.fps)
                    health_monitor.evaluate_camera_health(session, camera)
                    self.last_error_type = None
                except Exception as exc:
                    self.last_error_type = type(exc).__name__
                    logger.error(
                        "Camera health poll failed",
                        extra={"component": "camera_health", "camera_id": camera.id, "error_type": type(exc).__name__},
                    )
        finally:
            if owns_session:
                session.close()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                self.last_error_type = type(exc).__name__
                logger.error(
                    "Camera health cycle failed",
                    extra={"component": "camera_health", "error_type": type(exc).__name__},
                )
            self._stop_event.wait(self.interval_seconds)
        with self._state_lock:
            self.status = "STOPPED"


camera_health_worker = ContinuousCameraHealthWorker()
