"""Tests for model health honesty, bounded local discovery, and model validation.

No test in this file may require model weights to exist. Where a real model
would be needed, the load is exercised against a deliberately absent or
unreadable artifact and the resulting explicit state is asserted, because a test
that passed only when weights happened to be present would be a false assurance.
"""
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_models import ultralytics_adapter
from ai_models.ultralytics_adapter import (
    DISCOVERY_NOTE,
    UltralyticsModelAdapter,
    discover_local_models,
    missing_runtime_packages,
)
from app.config import settings
from scripts import validate_model


#: Every field a consumer of the model health payload may rely on.
REQUIRED_HEALTH_FIELDS = (
    "framework",
    "weights_path",
    "model_name",
    "model_version",
    "weights_checksum_sha256",
    "classes",
    "confidence_threshold",
    "device",
    "inference_fps",
    "latency_ms",
    "available",
    "status",
    "validation_state",
)


def test_model_health_reports_every_declared_metadata_field():
    """A health payload that omits a field is indistinguishable from a false one."""
    health = UltralyticsModelAdapter(None).health()

    assert set(REQUIRED_HEALTH_FIELDS) <= set(health)
    assert health["status"] == "MODEL_NOT_CONFIGURED"
    assert health["available"] is False
    assert health["weights_path"] is None
    assert health["framework"] == "UNCONFIGURED"


def test_unmeasured_rate_and_latency_are_null_not_zero():
    """Before any prediction, there is no rate. Zero would be a measurement."""
    adapter = UltralyticsModelAdapter("yolov8n.pt", "fixture-model", "fixture-version")

    health = adapter.health()

    assert health["inference_fps"] is None
    assert health["latency_ms"] is None
    assert health["average_inference_latency_ms"] is None
    assert health["inference_fps_samples"] == 0
    assert health["status"] == "NOT_LOADED"
    assert health["available"] is False


def test_health_never_claims_validation_it_cannot_perform():
    """Loading and timing a model is not validating it."""
    assert UltralyticsModelAdapter("yolov8n.pt", "m", "v").health()["validation_state"] == "NOT_VALIDATED"
    assert ultralytics_adapter.VALIDATION_STATE == "NOT_VALIDATED"


def test_inference_fps_stays_none_until_two_predictions_complete(monkeypatch):
    """A single completed call has no elapsed interval, so it has no rate."""
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    adapter = UltralyticsModelAdapter("yolov8n.pt", "m", "v")
    adapter._model = SimpleNamespace(predict=lambda *args, **kwargs: [], names={"a": "A"})

    adapter.predict("cam", datetime.now(timezone.utc), frame)
    assert adapter.health()["inference_fps"] is None

    adapter.predict("cam", datetime.now(timezone.utc), frame)
    health = adapter.health()
    assert health["inference_fps_samples"] == 2
    assert health["inference_fps"] is not None
    assert health["inference_fps"] > 0


def test_missing_runtime_dependency_is_named_and_never_installed(tmp_path, monkeypatch):
    """A format this machine cannot execute is reported, not faked."""
    weights = tmp_path / "detector.onnx"
    weights.write_bytes(b"onnx-placeholder-bytes")
    monkeypatch.setattr(ultralytics_adapter, "missing_runtime_packages", lambda suffix: ["onnxruntime"])

    adapter = UltralyticsModelAdapter(str(weights), "fixture-model", "fixture-version")
    loaded = adapter.load()

    health = adapter.health()
    assert loaded is False
    assert health["status"] == "MODEL_RUNTIME_DEPENDENCY_MISSING"
    assert health["missing_runtime_packages"] == ["onnxruntime"]
    assert health["framework"] == "ULTRALYTICS_ONNX"
    assert health["available"] is False


def test_missing_runtime_packages_reports_only_absent_distributions(monkeypatch):
    """Presence is decided by what is importable, not by what is configured."""
    monkeypatch.setattr(
        ultralytics_adapter.importlib.util,
        "find_spec",
        lambda name: object() if name in {"ultralytics", "torch"} else None,
    )
    assert missing_runtime_packages(".pt") == []
    assert missing_runtime_packages(".onnx") == ["onnxruntime"]


def test_unsupported_checkpoint_format_is_rejected_explicitly(tmp_path):
    weights = tmp_path / "detector.bin"
    weights.write_bytes(b"not-a-supported-format")

    adapter = UltralyticsModelAdapter(str(weights), "m", "v")
    adapter.load()

    assert adapter.health()["status"] == "MODEL_UNSUPPORTED_FORMAT"
    assert adapter.health()["framework"] == "UNSUPPORTED_FORMAT"


def test_discovery_finds_a_local_checkpoint_and_activates_nothing(tmp_path, monkeypatch):
    """Discovery reports real files; it never downloads and never activates."""
    nested = tmp_path / "checkpoints" / "line1" / "line2"
    nested.mkdir(parents=True)
    weights = nested / "ppe.pt"
    payload = b"local-checkpoint-bytes"
    weights.write_bytes(payload)
    monkeypatch.setattr(settings, "MODEL_SEARCH_PATHS", [str(tmp_path / "checkpoints")])
    monkeypatch.setattr(settings, "MODEL_AUTO_DISCOVER", True)

    candidates = discover_local_models()

    assert len(candidates) == 1
    candidate = candidates[0]

    assert candidate["path"] == str(weights.resolve())
    assert candidate["framework"] == "ULTRALYTICS_PYTHON"
    assert candidate["size_bytes"] == len(payload)
    assert candidate["weights_checksum_sha256"] == hashlib.sha256(payload).hexdigest()
    assert candidate["activation_state"] == "NOT_ACTIVATED"
    assert candidate["discovery_note"] == DISCOVERY_NOTE
    # Discovery is inert: it did not touch the configured weights path.
    assert settings.MODEL_WEIGHTS_PATH is None


def test_discovery_respects_max_depth_and_ignores_other_formats(tmp_path, monkeypatch):
    """A bounded scan cannot be used to sweep a whole volume."""
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (tmp_path / "shallow.pt").write_bytes(b"a")
    (deep / "too-deep.pt").write_bytes(b"b")
    (tmp_path / "notes.txt").write_text("not a checkpoint")
    (tmp_path / "model.bin").write_bytes(b"unsupported extension")
    monkeypatch.setattr(settings, "MODEL_SEARCH_PATHS", [str(tmp_path)])
    monkeypatch.setattr(settings, "MODEL_SEARCH_MAX_DEPTH", 2)

    candidates = discover_local_models()

    assert [Path(item["path"]).name for item in candidates] == ["shallow.pt"]


def test_discovery_returns_nothing_when_no_search_path_is_configured(monkeypatch):
    """An unconfigured deployment discovers nothing rather than guessing a path."""
    monkeypatch.setattr(settings, "MODEL_SEARCH_PATHS", [])
    monkeypatch.setattr(settings, "MODEL_AUTO_DISCOVER", False)

    assert discover_local_models() == []
    assert ultralytics_adapter.discovery_permission() == "MODEL_AUTO_DISCOVERY_DISABLED"


def test_validate_model_reports_not_configured_when_no_path_exists(monkeypatch):
    """No configured weights is an explicit state, not an invented path."""
    monkeypatch.setattr(settings, "MODEL_WEIGHTS_PATH", None)

    code, report, reason = validate_model.run_validation()

    assert code == validate_model.EXIT_BLOCKED
    assert reason == "MODEL_NOT_CONFIGURED"
    assert report["overall_state"] == "MODEL_NOT_CONFIGURED"
    # Every unrun check says so explicitly instead of implying a pass.
    assert {entry["state"] for entry in report["checks"]} == {validate_model.NOT_RUN}


def test_validate_model_reports_a_missing_file_and_runs_nothing_else(tmp_path):
    code, report, reason = validate_model.run_validation(str(tmp_path / "absent.pt"))

    assert code == validate_model.EXIT_BLOCKED
    assert reason == "MODEL_FILE_NOT_FOUND"
    assert report["overall_state"] == "MODEL_FILE_NOT_FOUND"
    states = {entry["step"]: entry["state"] for entry in report["checks"]}
    assert states["FILE_EXISTS"] == validate_model.FAIL
    assert states["MODEL_LOADS"] == validate_model.NOT_RUN
    assert states["LATENCY_MEASURED"] == validate_model.NOT_RUN
    # No rate and no latency may be reported for a model that never ran.
    assert report["throughput"] is None
    assert report["detection_quality_validated"] is False
    assert report["validation_state"] == "NOT_VALIDATED"


def test_validate_model_reports_an_empty_file_as_unreadable(tmp_path):
    empty = tmp_path / "empty.pt"
    empty.write_bytes(b"")

    code, report, reason = validate_model.run_validation(str(empty))

    assert code == validate_model.EXIT_BLOCKED
    assert reason == "MODEL_NOT_READABLE"
    states = {entry["step"]: entry["state"] for entry in report["checks"]}
    assert states["FILE_EXISTS"] == validate_model.FAIL or states["FILE_READABLE"] == validate_model.FAIL
    assert report["throughput"] is None


def test_validate_model_stops_at_a_runtime_dependency_that_is_absent(tmp_path, monkeypatch):
    """A missing runtime is named in the report; nothing is installed for it."""
    weights = tmp_path / "detector.onnx"
    weights.write_bytes(b"onnx-bytes")
    monkeypatch.setattr(validate_model.settings, "MODEL_NAME", "fixture-model")
    monkeypatch.setattr(validate_model.settings, "MODEL_VERSION", "fixture-version")
    monkeypatch.setattr(
        ultralytics_adapter, "missing_runtime_packages", lambda suffix: ["onnxruntime"]
    )

    code, report, reason = validate_model.run_validation(str(weights))

    assert code == validate_model.EXIT_FAILED
    assert reason == "MODEL_RUNTIME_DEPENDENCY_MISSING"
    loads = next(entry for entry in report["checks"] if entry["step"] == "MODEL_LOADS")
    assert loads["state"] == validate_model.FAIL
    assert loads["missing_runtime_packages"] == ["onnxruntime"]
    assert report["model"]["missing_runtime_packages"] == ["onnxruntime"]
    assert report["throughput"] is None


def test_validate_model_measures_only_executed_passes(monkeypatch):
    """Latency and FPS come from real calls, and zero passes never pass."""
    adapter = UltralyticsModelAdapter(None)

    calls: list[int] = []

    def fake_load() -> bool:
        adapter._model = SimpleNamespace(predict=lambda *args, **kwargs: [], names={"a": "A"})
        adapter.classes = ("A",)
        adapter.status = "READY"
        return True

    def fake_predict(camera_id, timestamp, frame):
        calls.append(1)
        adapter._completed_predictions.append(time.perf_counter())
        return []

    monkeypatch.setattr(adapter, "load", fake_load)
    monkeypatch.setattr(adapter, "predict", fake_predict)
    monkeypatch.setattr(validate_model, "build_adapter", lambda path: adapter)
    monkeypatch.setattr(validate_model.settings, "MODEL_NAME", "fixture-model")
    monkeypatch.setattr(validate_model.settings, "MODEL_VERSION", "fixture-version")
    monkeypatch.setattr(validate_model.settings, "MODEL_IMAGE_SIZE", 64)

    frame = validate_model.synthetic_probe_frame()
    assert frame.shape == (64, 64, 3)
    assert frame.max() == 0

    measurement = validate_model.measure_latency(adapter, frame, "probe", 2, 3)

    # Two discarded warm-up passes plus three timed passes, all really executed.
    assert len(calls) == 5
    assert measurement["completed_passes"] == 3
    assert measurement["latency_ms"] > 0
    assert measurement["inference_fps"] > 0
    assert measurement["probe_label"] == "SYNTHETIC_PROBE_NOT_REAL_FOOTAGE"


def test_validate_model_latency_check_fails_when_no_pass_completes(monkeypatch):
    """A probe that never completes cannot report a latency or a rate."""
    adapter = UltralyticsModelAdapter(None)
    monkeypatch.setattr(
        adapter, "predict", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    measurement = validate_model.measure_latency(
        adapter, np.zeros((8, 8, 3), dtype=np.uint8), "probe", 0, 3
    )

    assert measurement["completed_passes"] == 0
    assert measurement["latency_ms"] is None
    assert measurement["inference_fps"] is None
    assert measurement["error"] == "RuntimeError"


def test_empty_detection_result_is_a_result_not_a_schema_failure():
    """A blank probe frame legitimately finds nothing."""
    assert validate_model.detection_schema_errors([]) == []


def test_detection_schema_errors_are_reported_not_hidden():
    broken = [SimpleNamespace(class_name="", confidence=2.0, bbox=(1.0, 2.0))]
    errors = validate_model.detection_schema_errors(broken)

    assert any("class_name" in error for error in errors)
    assert any("confidence" in error for error in errors)
    assert any("bbox" in error for error in errors)


def test_validate_model_report_is_json_serialisable(tmp_path):
    """A report that cannot be written out is not a report an operator can read."""
    code, report, _reason = validate_model.run_validation(str(tmp_path / "absent.pt"))
    path = validate_model.save_report(report, tmp_path / "report.json")

    assert code == validate_model.EXIT_BLOCKED
    assert json.loads(path.read_text(encoding="utf-8"))["overall_state"] == "MODEL_FILE_NOT_FOUND"
