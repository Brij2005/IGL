from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_models.detection import Detection
from ai_models.ultralytics_adapter import UltralyticsModelAdapter
from app.config import DEVELOPMENT_SECRET_KEY, Settings
from app.services.tracker import IoUTracker
from app.schemas_camera import CameraOut, sanitize_stream_url
from app.utils.frame_buffer import FrameBuffer


def image(value=1, shape=(2, 2, 3)):
    return np.full(shape, value, dtype=np.uint8)


def detection(timestamp, bbox=(1, 1, 8, 8), camera_id="cam-1"):
    return Detection(
        camera_id=camera_id,
        timestamp=timestamp,
        frame_timestamp=timestamp,
        class_name="person",
        confidence=0.9,
        bbox=bbox,
        model_name="unit-test-model",
        model_version="test-only",
    )


def test_frame_buffer_empty_ordering_and_latest():
    now = datetime.now(timezone.utc)
    buffer = FrameBuffer(max_frames=5, max_bytes=1024)
    assert buffer.latest() is None
    buffer.add(now + timedelta(seconds=2), image(2))
    buffer.add(now, image(0))
    buffer.add(now + timedelta(seconds=1), image(1))
    assert [item.timestamp for item in buffer.snapshot()] == [
        now,
        now + timedelta(seconds=1),
        now + timedelta(seconds=2),
    ]
    assert buffer.latest().timestamp == now + timedelta(seconds=2)


def test_frame_buffer_retention_and_frame_count_limit():
    now = datetime.now(timezone.utc)
    buffer = FrameBuffer(retention_seconds=2, max_frames=2, max_bytes=1024)
    for offset in range(4):
        buffer.add(now + timedelta(seconds=offset), image(offset))
    assert [item.timestamp for item in buffer.snapshot()] == [
        now + timedelta(seconds=2),
        now + timedelta(seconds=3),
    ]


def test_frame_buffer_enforces_byte_limit_and_rejects_oversized_frames():
    now = datetime.now(timezone.utc)
    buffer = FrameBuffer(max_frames=10, max_bytes=24)
    buffer.add(now, image())
    buffer.add(now + timedelta(seconds=1), image(2))
    buffer.add(now + timedelta(seconds=2), image(3))
    assert len(buffer.snapshot()) == 2
    assert buffer.memory_bytes <= 24
    with pytest.raises(ValueError, match="memory limit"):
        buffer.add(now + timedelta(seconds=3), image(shape=(3, 3, 3)))
    with pytest.raises(ValueError, match="non-empty"):
        buffer.add(now, np.empty((0, 0, 3), dtype=np.uint8))


def test_frame_buffer_is_thread_safe_and_keeps_timestamp_order():
    now = datetime.now(timezone.utc)
    buffer = FrameBuffer(max_frames=500, max_bytes=100_000)

    def insert_batch(batch):
        for index in range(100):
            offset = batch * 100 + index
            buffer.add(now + timedelta(milliseconds=offset), image(offset % 255))

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(insert_batch, range(5)))

    timestamps = [item.timestamp for item in buffer.snapshot()]
    assert len(timestamps) == 500
    assert timestamps == sorted(timestamps)
    assert buffer.memory_bytes <= buffer.max_bytes


def test_frame_buffer_captures_pre_and_post_event_frames():
    now = datetime.now(timezone.utc)
    buffer = FrameBuffer(retention_seconds=20, max_frames=20, max_bytes=1024)
    for offset in range(-3, 1):
        buffer.add(now + timedelta(seconds=offset), image(offset + 3))
    recording = buffer.start_event_recording(now, pre_event_seconds=2, post_event_seconds=2)
    assert recording.status == "RECORDING"
    assert len(recording.frames) == 3
    buffer.add(now + timedelta(seconds=1), image(5))
    complete = buffer.get_recording(recording.recording_id)
    assert complete.status == "RECORDING"
    buffer.add(now + timedelta(seconds=2), image(6))
    complete = buffer.get_recording(recording.recording_id)
    assert complete.status == "COMPLETE"
    assert len(complete.frames) == 5


def test_frame_buffer_completes_when_timestamp_passes_post_event_deadline():
    now = datetime.now(timezone.utc)
    buffer = FrameBuffer(retention_seconds=20, max_frames=20, max_bytes=1024)
    buffer.add(now, image())
    recording = buffer.start_event_recording(now, pre_event_seconds=0, post_event_seconds=1)
    buffer.add(now + timedelta(seconds=2), image(2))
    complete = buffer.get_recording(recording.recording_id)
    assert complete.status == "COMPLETE"
    assert len(complete.frames) == 1


def test_frame_buffer_caps_active_recording_metadata():
    now = datetime.now(timezone.utc)
    buffer = FrameBuffer(max_frames=10, max_bytes=1024, max_recordings=1)
    first = buffer.start_event_recording(now, pre_event_seconds=0, post_event_seconds=5)
    with pytest.raises(RuntimeError, match="Maximum active"):
        buffer.start_event_recording(now, pre_event_seconds=0, post_event_seconds=5)
    assert first.status == "RECORDING"


def test_frame_buffer_cleanup_removes_expired_frames():
    now = datetime.now(timezone.utc)
    buffer = FrameBuffer(retention_seconds=2, max_frames=10, max_bytes=1024)
    buffer.add(now, image())
    buffer.cleanup(now + timedelta(seconds=3))
    assert buffer.snapshot() == ()


def test_model_adapter_reports_unconfigured_without_loading_or_inference():
    adapter = UltralyticsModelAdapter(None)
    assert adapter.load() is False
    assert adapter.health()["status"] == "MODEL_NOT_CONFIGURED"
    assert adapter.metadata()["available"] is False
    with pytest.raises(RuntimeError, match="MODEL_NOT_CONFIGURED"):
        adapter.predict("cam-1", datetime.now(timezone.utc), image())


def test_model_adapter_rejects_invalid_weights(tmp_path):
    weights = tmp_path / "missing.pt"
    adapter = UltralyticsModelAdapter(str(weights))
    assert adapter.load() is False
    assert adapter.health()["status"] == "MODEL_INVALID_WEIGHTS"
    weights.write_bytes(b"")
    assert adapter.load() is False
    assert adapter.health()["status"] == "MODEL_INVALID_WEIGHTS"
    architecture = tmp_path / "model.yaml"
    architecture.write_text("model: test-only", encoding="utf-8")
    assert UltralyticsModelAdapter(str(architecture), model_version="v1").load() is False


def test_model_adapter_requires_explicit_version_for_local_weights(tmp_path):
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"not-a-real-model")
    adapter = UltralyticsModelAdapter(str(weights))
    assert adapter.load() is False
    assert adapter.health()["status"] == "MODEL_VERSION_REQUIRED"


def test_model_adapter_reports_inference_errors_without_returning_detections():
    class FailingModel:
        def predict(self, *args, **kwargs):
            raise ValueError("test failure")

    adapter = UltralyticsModelAdapter(None)
    adapter._model = FailingModel()
    now = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError, match="inference failed"):
        adapter.predict("cam-1", now, image())
    assert adapter.health()["status"] == "INFERENCE_ERROR"


def test_camera_stream_urls_are_sanitized_for_api_responses():
    safe = sanitize_stream_url("rtsp://operator:secret@camera.example:554/live?token=abc&channel=2")
    assert safe == "rtsp://camera.example:554/live?token=REDACTED&channel=2"
    now = datetime.now(timezone.utc)
    camera = CameraOut.model_validate({
        "id": "camera-1",
        "name": "Test camera",
        "code": "CAM-1",
        "stream_url": "rtsp://operator:secret@camera.example/live",
        "camera_type": "RTSP",
        "fps": 25,
        "resolution": "1920x1080",
        "zone_id": None,
        "location_description": None,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    })
    assert camera.stream_url == "rtsp://camera.example/live"
    assert "secret" not in camera.model_dump_json()


def test_production_settings_reject_development_secret_and_wildcard_cors():
    with pytest.raises(ValueError, match="Production requires"):
        Settings(ENVIRONMENT="production")
    production = Settings(ENVIRONMENT="production", SECRET_KEY="x" * 40)
    assert production.SECRET_KEY.get_secret_value() == "x" * 40
    with pytest.raises(ValueError, match="Wildcard CORS"):
        Settings(BACKEND_CORS_ORIGINS=["*"])
    assert DEVELOPMENT_SECRET_KEY not in repr(Settings())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"confidence": 1.1},
        {"confidence": float("nan")},
        {"bbox": (1, 2, 1, 4)},
        {"bbox": (1, 2, float("inf"), 4)},
    ],
)
def test_detection_rejects_invalid_results(kwargs):
    now = datetime.now(timezone.utc)
    values = {
        "camera_id": "cam-1",
        "timestamp": now,
        "frame_timestamp": now,
        "class_name": "person",
        "confidence": 0.9,
        "bbox": (1, 2, 3, 4),
        "model_name": "test-model",
        "model_version": "test-version",
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        Detection(**values)


def test_tracker_lifecycle_and_visual_identity_only():
    now = datetime.now(timezone.utc)
    tracker = IoUTracker(max_lost_seconds=1.0)
    first = tracker.update("cam-1", [detection(now)], now)[0]
    assert first.lifecycle_state == "DETECTED"
    assert first.track_id.startswith("cam-1_track_")
    assert not hasattr(first, "employee_id")

    continued = tracker.update(
        "cam-1", [detection(now + timedelta(milliseconds=100), bbox=(2, 2, 9, 9))],
        now + timedelta(milliseconds=100),
    )[0]
    assert continued.track_id == first.track_id
    assert continued.lifecycle_state == "TRACKED"
    assert len(continued.trajectory) == 2

    lost = tracker.update("cam-1", [], now + timedelta(milliseconds=200))[0]
    assert lost.lifecycle_state == "TEMPORARILY_LOST"
    ended = tracker.update("cam-1", [], now + timedelta(seconds=2))[0]
    assert ended.lifecycle_state == "ENDED"