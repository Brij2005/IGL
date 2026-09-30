from io import BytesIO
import json
from pathlib import Path
import sys
import threading
import types
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_models.ultralytics_adapter import UltralyticsModelAdapter
from app.config import settings
from app.services.inference_pipeline import CameraInferencePipeline
from app.services.tracker import IoUTracker
from app.services.video_ingestion import StreamReader
from app.utils.frame_buffer import FrameBuffer
from app.utils.redaction import safe_source_identifier
from scripts import run_inference


def test_missing_source_and_model_are_reported_without_starting_pipeline(monkeypatch, capsys):
    monkeypatch.setattr(settings, "VIDEO_SOURCE", None)
    monkeypatch.setattr(settings, "RTSP_URL", None)
    monkeypatch.setattr(settings, "MODEL_WEIGHTS_PATH", None)
    assert run_inference.main([]) == 2
    output = capsys.readouterr().err
    assert "VIDEO_SOURCE_NOT_CONFIGURED" in output
    assert "MODEL_NOT_CONFIGURED" in output


def test_missing_model_configuration_is_explicit():
    adapter = UltralyticsModelAdapter(None)
    assert run_inference.validate_model_configuration(adapter) == "MODEL_NOT_CONFIGURED"


def test_invalid_model_path_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MODEL_NAME", "configured-model")
    monkeypatch.setattr(settings, "MODEL_VERSION", "authorized-version")
    adapter = UltralyticsModelAdapter(str(tmp_path / "missing.pt"), "configured-model", "authorized-version")
    assert run_inference.validate_model_configuration(adapter) == "MODEL_INVALID_WEIGHTS"


def test_model_loading_failure_is_explicit_and_does_not_fallback(monkeypatch, tmp_path):
    weights = tmp_path / "provided.pt"

    class ReadableCheckpointPath:
        suffix = ".pt"

        def is_file(self):
            return True

        def stat(self):
            return SimpleNamespace(st_size=1)

        def open(self, mode):
            return BytesIO(b"x")

        def __str__(self):
            return str(weights)

    def fail_load(path):
        raise RuntimeError("model load failure")

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=fail_load))
    monkeypatch.setattr(settings, "MODEL_NAME", "configured-model")
    monkeypatch.setattr(settings, "MODEL_VERSION", "authorized-version")
    adapter = UltralyticsModelAdapter(str(weights), "configured-model", "authorized-version")
    adapter.weights_path = ReadableCheckpointPath()
    assert run_inference.validate_model_configuration(adapter) == "MODEL_LOAD_FAILED"
    assert adapter.health()["available"] is False


def test_source_configuration_accepts_explicit_rtsp_without_connecting(monkeypatch):
    monkeypatch.setattr(settings, "VIDEO_SOURCE", None)
    monkeypatch.setattr(settings, "RTSP_URL", SecretStr("rtsp://camera.invalid/live"))
    configured = run_inference.resolve_source()
    assert run_inference.validate_source(configured) == "RTSP"
    assert run_inference.source_kind(configured) == "RTSP"


def test_source_conflict_and_invalid_local_video_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "VIDEO_SOURCE", "local.mp4")
    monkeypatch.setattr(settings, "RTSP_URL", SecretStr("rtsp://camera.invalid/live"))
    with pytest.raises(run_inference.ValidationError, match="VIDEO_SOURCE_CONFIGURATION_CONFLICT"):
        run_inference.resolve_source()
    with pytest.raises(run_inference.ValidationError, match="INVALID_VIDEO_SOURCE"):
        run_inference.validate_source(str(tmp_path / "missing.mp4"))


def test_source_identifier_redacts_userinfo_and_query_values():
    placeholder_user, placeholder_password, placeholder_token = "u", "p", "v"
    source = f"rtsp://{placeholder_user}:{placeholder_password}@camera.invalid/live?token={placeholder_token}"
    identifier = safe_source_identifier(source)
    assert identifier == "rtsp://***:***@camera.invalid/live"
    assert f"{placeholder_user}:{placeholder_password}@" not in identifier
    assert f"token={placeholder_token}" not in identifier


def test_pipeline_and_reader_shutdown_gracefully_without_source(tmp_path):
    model = UltralyticsModelAdapter(None)
    tracker = IoUTracker()
    reader = StreamReader("validation-camera", str(tmp_path / "missing.mp4"), frame_buffer=FrameBuffer())
    pipeline = CameraInferencePipeline("validation-camera", model, tracker, threading.Lock())
    reader.start()
    pipeline.start(reader)
    reader._thread.join(timeout=5)
    pipeline._thread.join(timeout=5)
    pipeline.stop()
    reader.stop()
    assert not reader._thread.is_alive()
    assert not pipeline._thread.is_alive()
    assert reader.status == "STOPPED"
    assert tracker.metrics()["unique_track_count"] == 0


def test_report_contains_only_safe_source_identifier_and_no_igl_claim():
    source_user, source_password = "u", "p"
    source = f"rtsp://{source_user}:{source_password}@camera.invalid/live?token=x"
    model = UltralyticsModelAdapter(None)
    report = run_inference.create_report(
        source=source,
        source_type="RTSP",
        source_identifier=safe_source_identifier(source),
        model=model,
        reader=None,
        pipeline=None,
        processing_seconds=0,
        validation_status="NOT_VALIDATED",
        errors=[],
    )
    serialized = json.dumps(report)
    assert f"{source_user}:{source_password}@" not in serialized
    assert "token=x" not in serialized
    assert report["igl_validated"] is False
    assert report["validation_status"] == "NOT_VALIDATED"
