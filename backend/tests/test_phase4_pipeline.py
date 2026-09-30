from pathlib import Path
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_models.ultralytics_adapter import UltralyticsModelAdapter
from app.services.inference_pipeline import CameraInferencePipeline
from app.services.tracker import IoUTracker
from app.services.video_ingestion import StreamReader
from app.utils.frame_buffer import FrameBuffer
import numpy as np
from fastapi.testclient import TestClient
from app.main import app


def test_invalid_video_stops_without_fake_inference_or_detections(tmp_path):
    model = UltralyticsModelAdapter(None)
    assert model.load() is False
    tracker = IoUTracker()
    reader = StreamReader(
        camera_id="test-camera",
        stream_url=str(tmp_path / "missing-test-video.mp4"),
        frame_buffer=FrameBuffer(max_frames=10, max_bytes=1024),
    )
    pipeline = CameraInferencePipeline("test-camera", model, tracker, threading.Lock())
    try:
        reader.start()
        reader._thread.join(timeout=5)
        pipeline.start(reader)
        pipeline._thread.join(timeout=5)
        state = pipeline.status()
        assert reader.status == "SOURCE_UNAVAILABLE"
        assert state["inference_status"] == "MODEL_NOT_CONFIGURED"
        assert state["inferences_completed"] == 0
        assert state["frames_seen"] == 0
        assert tracker.get_tracks("test-camera") == ()
    finally:
        pipeline.stop()
        reader.stop()


def test_pipeline_rejects_empty_frame_and_reports_failure():
    model = UltralyticsModelAdapter(None)
    model.load()
    pipeline = CameraInferencePipeline("test-camera", model, IoUTracker(), threading.Lock())
    assert pipeline.process_frame(datetime.now(timezone.utc), np.empty((0, 0, 3), dtype=np.uint8)) == []
    state = pipeline.status()
    assert state["inference_status"] == "MODEL_NOT_CONFIGURED"
    assert state["inference_failures"] == 1


def test_inference_health_endpoints_reject_unauthenticated_requests():
    with TestClient(app) as client:
        assert client.get("/api/v1/system/ai-health").status_code in (401, 403)
        assert client.get("/api/v1/system/pipelines").status_code in (401, 403)


def test_root_health_does_not_claim_online_when_model_is_unconfigured():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    # The process being up is reported as its own state and never as health.
    assert payload["application"] == "APPLICATION_UP"
    assert payload["overall_status"] == "DEGRADED"
    assert payload["model_state"] == "MODEL_NOT_CONFIGURED"
    assert payload["database"] == "DATABASE_OK"
    assert payload["migrations"] == "MIGRATIONS_CURRENT"
    assert payload["camera_state"] == "NO_CAMERA"
    assert payload["validation_status"] == "NOT_VALIDATED"
    assert payload["igl_validated"] is False
    assert payload["measured_performance"] == "NOT_MEASURED_WITHOUT_OBSERVED_FRAMES"
    assert "MODEL_NOT_CONFIGURED" in payload["degraded_reasons"]
    assert "status" not in payload