"""Per-camera frame-to-model-to-visual-tracking pipeline."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

import numpy as np

from ai_models.ultralytics_adapter import UltralyticsModelAdapter

try:
    from app.config import settings
    from app.services.tracker import IoUTracker
    from app.services.video_ingestion import StreamReader, ingestion_manager
    from app.utils.frame_buffer import FrameBuffer
except ImportError:
    from backend.app.config import settings
    from backend.app.services.tracker import IoUTracker
    from backend.app.services.video_ingestion import StreamReader, ingestion_manager
    from backend.app.utils.frame_buffer import FrameBuffer


logger = logging.getLogger("igl.ai.pipeline")


class CameraInferencePipeline:
    def __init__(
        self,
        camera_id: str,
        model: UltralyticsModelAdapter,
        tracker: IoUTracker,
        inference_lock: threading.Lock,
        frame_buffer: FrameBuffer | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.model = model
        self.tracker = tracker
        self.inference_lock = inference_lock
        self.frame_buffer = frame_buffer or FrameBuffer(
            settings.FRAME_BUFFER_RETENTION_SECONDS,
            settings.FRAME_BUFFER_MAX_FRAMES,
            settings.FRAME_BUFFER_MAX_BYTES,
        )
        self.reader: StreamReader | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_frame_timestamp: datetime | None = None
        self._last_error_type: str | None = None
        self._frames_seen = 0
        self._inferences_attempted = 0
        self._inferences_completed = 0
        self._inference_failures = 0
        self._tracking_failures = 0
        self._detection_count = 0
        self._last_inference_timestamp: datetime | None = None
        self._state = "STOPPED"
        self._lock = threading.RLock()

    def start(self, reader: StreamReader) -> None:
        with self._lock:
            if self._running:
                return
            self.reader = reader
            self.frame_buffer = reader.frame_buffer
            self._running = True
            self._stop_event.clear()
            self._state = "STARTING"
            self._thread = threading.Thread(target=self._run, name=f"infer-{self.camera_id}", daemon=True)
            self._thread.start()

    def process_frame(self, timestamp: datetime, frame: np.ndarray, *, already_buffered: bool = False) -> list:
        """Process one decoded frame; never fabricates results when a model is absent."""
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            self._record_failure("EmptyFrame")
            return []
        if not already_buffered:
            try:
                self.frame_buffer.add(timestamp, frame)
            except (ValueError, RuntimeError) as exc:
                self._record_failure(type(exc).__name__)
                logger.error(
                    "Frame rejected by inference pipeline",
                    extra={"component": "inference_pipeline", "camera_id": self.camera_id, "error_type": type(exc).__name__},
                )
                return []
        self._frames_seen += 1
        if not self.model.health()["available"]:
            with self._lock:
                self._state = self.model.health()["status"]
            return []
        try:
            self._inferences_attempted += 1
            with self.inference_lock:
                detections = self.model.predict(self.camera_id, timestamp, frame)
        except Exception as exc:
            self._inference_failures += 1
            self._last_error_type = type(exc).__name__
            with self._lock:
                self._state = "INFERENCE_ERROR"
            logger.error(
                "Frame inference failed",
                extra={"component": "inference_pipeline", "camera_id": self.camera_id, "error_type": type(exc).__name__},
            )
            return []
        self._inferences_completed += 1
        self._last_inference_timestamp = timestamp
        self._detection_count += len(detections)
        try:
            self.tracker.update(self.camera_id, detections, timestamp)
        except Exception as exc:
            self._tracking_failures += 1
            self._last_error_type = type(exc).__name__
            with self._lock:
                self._state = "TRACKING_ERROR"
            logger.error(
                "Frame tracking failed",
                extra={"component": "inference_pipeline", "camera_id": self.camera_id, "error_type": type(exc).__name__},
            )
            return detections
        with self._lock:
            self._state = "RUNNING"
            self._last_error_type = None
        return detections

    def status(self) -> dict[str, Any]:
        with self._lock:
            model_status = self.model.health()["status"]
            inference_status = self._state
            if not self._running and inference_status == "STOPPED":
                inference_status = "NOT_RUNNING"
            elif not self.model.health()["available"]:
                inference_status = model_status
            return {
                "camera_id": self.camera_id,
                "pipeline_state": self._state,
                "inference_status": inference_status,
                "stream_status": self.reader.status if self.reader else "NOT_STARTED",
                "frames_seen": self._frames_seen,
                "inferences_attempted": self._inferences_attempted,
                "inferences_completed": self._inferences_completed,
                "inference_failures": self._inference_failures,
                "tracking_failures": self._tracking_failures,
                "detection_count": self._detection_count,
                "last_frame_timestamp": self._last_frame_timestamp,
                "last_inference_timestamp": self._last_inference_timestamp,
                "last_error_type": self._last_error_type,
                "buffer_frames": len(self.frame_buffer.snapshot()),
                "buffer_memory_bytes": self.frame_buffer.memory_bytes,
            }

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        with self._lock:
            self._state = "STOPPED"

    def _run(self) -> None:
        while not self._stop_event.wait(0.02):
            reader = self.reader
            if reader is None:
                break
            for buffered in reader.get_frames_after(self._last_frame_timestamp):
                self._last_frame_timestamp = buffered.timestamp
                self.process_frame(buffered.timestamp, buffered.image, already_buffered=True)
            if not reader._running and reader.status in ("SOURCE_UNAVAILABLE", "END_OF_FILE", "STOPPED"):
                if not reader.get_frames_after(self._last_frame_timestamp):
                    break
        self._running = False
        with self._lock:
            if self._state in ("STARTING", "MODEL_NOT_CONFIGURED", "MODEL_INVALID_WEIGHTS", "NOT_LOADED"):
                if self._state == "STARTING":
                    self._state = self.model.health()["status"]

    def _record_failure(self, error_type: str) -> None:
        self._inference_failures += 1
        self._last_error_type = error_type
        with self._lock:
            self._state = "INFERENCE_ERROR"


class InferencePipelineManager:
    def __init__(self) -> None:
        self.model = UltralyticsModelAdapter(
            settings.MODEL_WEIGHTS_PATH,
            settings.MODEL_NAME,
            settings.MODEL_VERSION,
            settings.MODEL_CONFIDENCE_THRESHOLD,
            settings.MODEL_DEVICE,
        )
        self.tracker = IoUTracker()
        self._inference_lock = threading.Lock()
        self._pipelines: dict[str, CameraInferencePipeline] = {}
        self._lock = threading.RLock()
        self._model_prepared = False

    def prepare_model(self) -> dict[str, Any]:
        with self._lock:
            if not self._model_prepared:
                self.model.load()
                self._model_prepared = True
            return self.model.health()

    def start_stream(self, camera_id: str, stream_url: str, target_fps: float = 25.0) -> CameraInferencePipeline:
        with self._lock:
            current = self._pipelines.get(camera_id)
            if current and current.reader and current.reader.stream_url == stream_url and current.status()["pipeline_state"] != "STOPPED":
                return current
            self.stop_stream(camera_id)
            self.prepare_model()
            reader = ingestion_manager.start_stream(camera_id, stream_url, target_fps)
            pipeline = CameraInferencePipeline(camera_id, self.model, self.tracker, self._inference_lock)
            pipeline.start(reader)
            self._pipelines[camera_id] = pipeline
            return pipeline

    def stop_stream(self, camera_id: str) -> None:
        with self._lock:
            pipeline = self._pipelines.pop(camera_id, None)
            if pipeline:
                pipeline.stop()
            ingestion_manager.stop_stream(camera_id)

    def stop_all(self) -> None:
        with self._lock:
            for camera_id in list(self._pipelines):
                self.stop_stream(camera_id)
            ingestion_manager.stop_all()

    def model_health(self) -> dict[str, Any]:
        return self.model.health()

    def pipeline_status(self, camera_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            pipelines = list(self._pipelines.values())
        return [pipeline.status() for pipeline in pipelines if camera_id is None or pipeline.camera_id == camera_id]


pipeline_manager = InferencePipelineManager()