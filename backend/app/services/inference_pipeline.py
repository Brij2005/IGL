"""Per-camera frame-to-model-to-visual-tracking pipeline.

An operator who stops a camera means it. That intent is persisted outside the
database, because no new column or migration may be introduced for it, so the
background health worker does not quietly resume a camera an operator
deliberately stopped after a backend restart. The marker file records nothing
but the camera id and the moment of the stop; it never holds configuration.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from sqlalchemy.engine import make_url

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

#: File that records cameras an operator stopped on purpose. It sits beside the
#: data directory, which is git-ignored and holds deployment-local state only.
OPERATOR_STOP_MARKER_FILENAME = "camera_operator_stops.json"


def _database_directory() -> Path | None:
    """Return the directory of the configured SQLite database, if there is one."""
    try:
        url = make_url(settings.DATABASE_URL.get_secret_value())
    except Exception:  # noqa: BLE001 - an unparseable URL is not a reason to crash
        return None
    if url.get_backend_name() != "sqlite":
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return Path(database).expanduser().parent


def operator_stop_marker_path() -> Path:
    """Return the JSON marker file that records intentional operator stops."""
    database_directory = _database_directory()
    if database_directory is not None:
        return database_directory / OPERATOR_STOP_MARKER_FILENAME
    return Path(__file__).resolve().parents[3] / "data" / OPERATOR_STOP_MARKER_FILENAME


def load_operator_stop_markers() -> dict[str, str]:
    """Return the persisted operator stops, keyed by camera id.

    An absent, unreadable, or corrupt marker file yields an empty set rather than
    an error: losing a record of an intentional stop is bad, but refusing to
    start because a state file is damaged would be worse.
    """
    path = operator_stop_marker_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.error(
            "Operator stop markers could not be read",
            extra={"component": "inference_pipeline", "error_type": type(exc).__name__},
        )
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(
            "Operator stop markers are not valid JSON and are being ignored",
            extra={"component": "inference_pipeline", "marker_file": str(path)},
        )
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def save_operator_stop_markers(markers: dict[str, str]) -> bool:
    """Write the operator stop markers atomically. Returns whether it succeeded."""
    path = operator_stop_marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(markers, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        logger.error(
            "Operator stop markers could not be written",
            extra={"component": "inference_pipeline", "error_type": type(exc).__name__},
        )
        return False
    return True



def build_observation_sink(
    camera_id: str,
    model_health: Callable[[], dict[str, Any]],
) -> Callable[[datetime, np.ndarray, list, tuple], None] | None:
    """Return a sink that persists detections and evaluates configured detectors.

    The sink opens its own short-lived session so the ingestion thread never
    shares a database session with a request. It returns None when no database is
    reachable, which leaves the pipeline in an explicitly non-persisting state
    rather than pretending detections were recorded.
    """
    try:
        from app.database import SessionLocal
    except ImportError:
        try:
            from backend.app.database import SessionLocal
        except ImportError:  # pragma: no cover
            return None

    from app.models import Camera
    from app.services.safety_orchestrator import safety_orchestrator

    def sink(timestamp: datetime, frame: np.ndarray, detections: list, tracks: tuple) -> None:
        db = SessionLocal()
        try:
            camera = db.query(Camera).filter(Camera.id == camera_id, Camera.is_active.is_(True)).first()
            if camera is None:
                # The camera is no longer active or was removed; there is nothing
                # to attribute an observation to.
                return
            safety_orchestrator.persist_detections(db, camera, detections, tracks)
            safety_orchestrator.evaluate_frame(
                db,
                camera,
                timestamp=timestamp,
                frame=frame,
                detections=detections,
                tracks=tracks,
                model_health=model_health(),
            )
        finally:
            db.close()

    return sink


class CameraInferencePipeline:
    def __init__(
        self,
        camera_id: str,
        model: UltralyticsModelAdapter,
        tracker: IoUTracker,
        inference_lock: threading.Lock,
        frame_buffer: FrameBuffer | None = None,
        observation_sink: Callable[[datetime, np.ndarray, list, tuple], None] | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.model = model
        self.tracker = tracker
        self.inference_lock = inference_lock
        # Optional database-backed sink that persists detections and evaluates
        # configured detectors. It is absent in contexts without a session (for
        # example unit tests), in which case detections stay in memory only and
        # no event can be created.
        self.observation_sink = observation_sink
        self._observations_delivered = 0
        self._observation_sink_failures = 0
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
            tracks = self.tracker.update(self.camera_id, detections, timestamp)
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
        self._deliver_observations(timestamp, frame, detections, tracks)
        return detections

    def _deliver_observations(self, timestamp, frame, detections, tracks) -> None:
        """Hand real detections to the safety orchestrator.

        A sink failure is recorded and does not change the detection result: the
        frame was still observed, so hiding it would be the less truthful option.
        """
        if self.observation_sink is None:
            return
        try:
            self.observation_sink(timestamp, frame, detections, tracks)
            self._observations_delivered += 1
        except Exception as exc:
            self._observation_sink_failures += 1
            self._last_error_type = type(exc).__name__
            logger.error(
                "Safety observation delivery failed",
                extra={
                    "component": "inference_pipeline",
                    "camera_id": self.camera_id,
                    "error_type": type(exc).__name__,
                },
            )

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
                "observations_delivered": self._observations_delivered,
                "observation_sink_failures": self._observation_sink_failures,
                "observation_sink_configured": self.observation_sink is not None,
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
        # Hydrated from disk so a backend restart does not resume a camera the
        # operator deliberately stopped.
        self._operator_stopped: set[str] = set(load_operator_stop_markers())
        self._lock = threading.RLock()
        self._model_prepared = False

    def prepare_model(self) -> dict[str, Any]:
        with self._lock:
            if not self._model_prepared:
                self.model.load()
                self._model_prepared = True
            return self.model.health()

    def start_stream(
        self,
        camera_id: str,
        stream_url: str,
        target_fps: float = 25.0,
        *,
        operator_start: bool = False,
    ) -> CameraInferencePipeline | None:
        with self._lock:
            if camera_id in self._operator_stopped and not operator_start:
                return self._pipelines.get(camera_id)
            if operator_start:
                self.clear_operator_stop(camera_id)
            current = self._pipelines.get(camera_id)
            if current and current.reader and current.reader.stream_url == stream_url and current.status()["pipeline_state"] != "STOPPED":
                return current
            # A camera that already had a pipeline is being reopened, even after
            # stop_stream removed its reader from the ingestion manager.
            reopening = current is not None or ingestion_manager.get_reader(camera_id) is not None
            self.stop_stream(camera_id)
            self.prepare_model()
            reader = ingestion_manager.start_stream(camera_id, stream_url, target_fps, reopen=reopening)
            pipeline = CameraInferencePipeline(
                camera_id,
                self.model,
                self.tracker,
                self._inference_lock,
                observation_sink=build_observation_sink(camera_id, self.model.health),
            )
            pipeline.start(reader)
            self._pipelines[camera_id] = pipeline
            return pipeline

    def stop_stream(self, camera_id: str, *, operator_initiated: bool = False) -> None:
        with self._lock:
            if operator_initiated:
                self.record_operator_stop(camera_id)
            pipeline = self._pipelines.pop(camera_id, None)
            if pipeline:
                pipeline.stop()
            ingestion_manager.stop_stream(camera_id)

    def record_operator_stop(self, camera_id: str) -> None:
        """Persist an intentional stop so a restart does not resume this camera.

        Persisted outside the database because no column may be added for it. A
        failure to write is logged, never reported as a stored stop: the
        in-memory latch still holds for this process.
        """
        with self._lock:
            self._operator_stopped.add(camera_id)
            markers = {key: value for key, value in load_operator_stop_markers().items() if key != camera_id}
            markers[camera_id] = datetime.now(timezone.utc).isoformat()
            save_operator_stop_markers(markers)

    def clear_operator_stop(self, camera_id: str) -> None:
        """Forget an intentional stop because the operator has started the camera."""
        with self._lock:
            self._operator_stopped.discard(camera_id)
            markers = load_operator_stop_markers()
            if camera_id in markers:
                markers.pop(camera_id)
                save_operator_stop_markers(markers)

    def is_operator_stopped(self, camera_id: str) -> bool:
        with self._lock:
            return camera_id in self._operator_stopped

    def stop_all(self) -> None:
        """Tear down every pipeline, keeping recorded operator stops intact.

        A shutdown is not an operator decision about any individual camera, so
        the persisted stops are deliberately left in place; the next process
        reads them and still honours them.
        """
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