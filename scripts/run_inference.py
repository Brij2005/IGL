"""Run and report the existing Phase 4 video inference pipeline."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import platform
import sys
import threading
import time
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for import_path in (str(PROJECT_ROOT), str(BACKEND_ROOT)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

import cv2
import numpy as np

from ai_models.ultralytics_adapter import UltralyticsModelAdapter, weights_checksum
from app.config import settings
from app.services.inference_pipeline import CameraInferencePipeline
from app.services.tracker import IoUTracker
from app.services.video_ingestion import StreamReader
from app.utils.redaction import safe_source_identifier


LOGGER = logging.getLogger("igl.validation")
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}
VALIDATION_STATUSES = {
    "NOT_VALIDATED",
    "TECHNICAL_PIPELINE_VALIDATED",
    "REAL_INPUT_VALIDATED",
    "IGL_VALIDATED",
    "TEST_FIXTURE_EXECUTION",
}
AUTHORIZED_REAL_INPUT = "AUTHORIZED_REAL_INPUT"
TEST_FIXTURE = "TEST_FIXTURE"
INPUT_PROVENANCES = {AUTHORIZED_REAL_INPUT, TEST_FIXTURE}


class ValidationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def resolve_source(cli_source: str | None = None) -> str:
    if cli_source and cli_source.strip():
        return cli_source.strip()
    configured = []
    if settings.VIDEO_SOURCE and settings.VIDEO_SOURCE.strip():
        configured.append(settings.VIDEO_SOURCE.strip())
    if settings.RTSP_URL and settings.RTSP_URL.get_secret_value().strip():
        configured.append(settings.RTSP_URL.get_secret_value().strip())
    if not configured:
        raise ValidationError("VIDEO_SOURCE_NOT_CONFIGURED")
    if len(configured) > 1:
        raise ValidationError("VIDEO_SOURCE_CONFIGURATION_CONFLICT")
    return configured[0]


def source_kind(source: str) -> str:
    scheme = urlsplit(source).scheme.lower()
    if scheme in {"rtsp", "rtsps"}:
        return "RTSP"
    if scheme in {"http", "https"}:
        return "IP_STREAM"
    return "LOCAL_VIDEO"


def validate_source(source: str) -> str:
    kind = source_kind(source)
    if kind in {"RTSP", "IP_STREAM"}:
        try:
            parsed = urlsplit(source)
            valid_host = bool(parsed.hostname)
            parsed.port
        except ValueError as exc:
            raise ValidationError("INVALID_VIDEO_SOURCE") from exc
        if not valid_host:
            raise ValidationError("INVALID_VIDEO_SOURCE")
        return kind
    path = Path(source).expanduser()
    if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
        raise ValidationError("INVALID_VIDEO_SOURCE")
    try:
        with path.open("rb") as video_file:
            video_file.read(1)
    except OSError as exc:
        raise ValidationError("VIDEO_SOURCE_NOT_READABLE") from exc
    return kind


def build_model_adapter() -> UltralyticsModelAdapter:
    return UltralyticsModelAdapter(
        weights_path=settings.MODEL_WEIGHTS_PATH,
        model_name=settings.MODEL_NAME,
        model_version=settings.MODEL_VERSION,
        confidence_threshold=settings.MODEL_CONFIDENCE_THRESHOLD,
        device=settings.MODEL_DEVICE,
    )


def validate_model_configuration(adapter: UltralyticsModelAdapter) -> str | None:
    if adapter.weights_path is None:
        return "MODEL_NOT_CONFIGURED"
    if not settings.MODEL_NAME or not settings.MODEL_NAME.strip():
        return "MODEL_NAME_NOT_CONFIGURED"
    if not settings.MODEL_VERSION or not settings.MODEL_VERSION.strip():
        return "MODEL_VERSION_REQUIRED"
    if not adapter.load():
        return adapter.health()["status"]
    return None


def package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "NOT_INSTALLED"


def create_report(
    *,
    source: str,
    source_type: str,
    model: UltralyticsModelAdapter,
    reader: StreamReader | None,
    pipeline: CameraInferencePipeline | None,
    processing_seconds: float,
    validation_status: str,
    errors: list[str],
    stream_terminal_state: str | None = None,
    input_provenance: str = AUTHORIZED_REAL_INPUT,
) -> dict[str, Any]:
    """Assemble a complete report.

    The report is final at construction time: the redacted source identifier and
    the stream terminal state are computed here rather than patched in later.
    A ``REAL_INPUT_VALIDATED`` status is rejected unless the run used an
    authorized real input, actually processed frames, actually completed
    inference, and recorded no errors, so a report can never claim real-input
    validation that did not happen.
    """
    if validation_status not in VALIDATION_STATUSES:
        raise ValueError("Invalid validation status")
    if input_provenance not in INPUT_PROVENANCES:
        raise ValueError("Invalid input provenance")

    reader_frames = reader.total_frames_read if reader else 0
    pipeline_status = pipeline.status() if pipeline else {}
    processed_frames = int(pipeline_status.get("frames_seen", 0))
    inference_count = model.inference_count
    tracking_metrics = pipeline.tracker.metrics() if pipeline else {"unique_track_count": 0, "lifecycle_transitions": {}}
    processing_fps = round(processed_frames / processing_seconds, 3) if processing_seconds > 0 else None

    if validation_status == "REAL_INPUT_VALIDATED":
        if input_provenance != AUTHORIZED_REAL_INPUT:
            raise ValueError("REAL_INPUT_VALIDATED requires an authorized real input, not a test fixture")
        if errors:
            raise ValueError("REAL_INPUT_VALIDATED cannot be reported while errors are recorded")
        if reader_frames <= 0 or processed_frames <= 0 or inference_count <= 0:
            raise ValueError("REAL_INPUT_VALIDATED requires observed frames and completed inference")
        if int(pipeline_status.get("inferences_completed", 0)) <= 0:
            raise ValueError("REAL_INPUT_VALIDATED requires at least one completed inference, not only an attempt")

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_provenance": input_provenance,
        "environment": {
            "os": platform.platform(),
            "python_version": platform.python_version(),
            "opencv_version": cv2.__version__,
            "pytorch_version": package_version("torch"),
            "ultralytics_version": package_version("ultralytics"),
        },
        "model": {
            "name": settings.MODEL_NAME or model.model_name,
            "version": settings.MODEL_VERSION or model.model_version,
            "weights_identifier": model.weights_path.name if model.weights_path else None,
            "weights_checksum_sha256": weights_checksum(model.weights_path),
            "confidence_threshold": model.confidence_threshold,
            "device": model.device,
            "load_status": model.health()["status"],
            "class_names": model.health()["classes"],
        },
        "input": {
            "source_type": source_type,
            "source_identifier": safe_source_identifier(source),
            "resolution": list(reader.source_resolution) if reader and reader.source_resolution else None,
            "source_fps": reader.source_fps if reader else None,
            "duration_processed_seconds": round(processing_seconds, 3),
            "source_duration_seconds": reader.source_duration_seconds if reader else None,
            "connection_established": reader._has_connected if reader else False,
            "first_frame_timestamp": reader.first_frame_timestamp.isoformat() if reader and reader.first_frame_timestamp else None,
            "last_frame_timestamp": reader.last_frame_timestamp.isoformat() if reader and reader.last_frame_timestamp else None,
            "stream_terminal_state": stream_terminal_state,
        },
        "pipeline": {
            "frames_received": reader_frames,
            "frames_processed": processed_frames,
            "inference_count": inference_count,
            "inference_attempt_count": model.health().get("inference_attempt_count", inference_count),
            "inference_failure_count": model.health().get("inference_failure_count", 0),
            "inferences_completed": int(pipeline_status.get("inferences_completed", 0)),
            "average_inference_latency_ms": model.health()["average_inference_latency_ms"],
            "processing_fps": processing_fps,
            "detection_count": int(pipeline_status.get("detection_count", 0)),
            "unique_track_count": int(tracking_metrics["unique_track_count"]),
            "track_lifecycle_transitions": tracking_metrics["lifecycle_transitions"],
        },
        "errors": {
            "dropped_frames": reader.dropped_frames_count if reader else 0,
            "inference_failures": int(pipeline_status.get("inference_failures", 0)),
            "tracking_failures": int(pipeline_status.get("tracking_failures", 0)),
            "stream_reconnects": reader.reconnects if reader else 0,
            "pipeline_errors": errors,
        },
        "validation_status": validation_status,
        "igl_validated": False,
    }
    return report


def create_blocked_report(
    *,
    source: str,
    source_type: str,
    model: UltralyticsModelAdapter,
    reason: str,
    input_provenance: str = AUTHORIZED_REAL_INPUT,
) -> dict[str, Any]:
    """Assemble a report for a run that could not start; no pipeline work is claimed."""
    return create_report(
        source=source,
        source_type=source_type,
        model=model,
        reader=None,
        pipeline=None,
        processing_seconds=0.0,
        validation_status="NOT_VALIDATED",
        errors=[reason],
        stream_terminal_state="NOT_STARTED",
        input_provenance=input_provenance,
    )


def save_report(report: dict[str, Any], report_path: str | Path | None = None) -> Path:
    target = Path(report_path) if report_path else (
        PROJECT_ROOT / "data" / "validation_reports" /
        f"phase4_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)
    return target


def run_validation(
    source: str,
    source_type: str,
    model: UltralyticsModelAdapter,
    duration_seconds: float | None,
    report_path: str | Path | None,
    input_provenance: str = AUTHORIZED_REAL_INPUT,
) -> tuple[int, dict[str, Any] | None, str | None]:
    if input_provenance not in INPUT_PROVENANCES:
        raise ValueError("Invalid input provenance")

    model_error = validate_model_configuration(model)
    if model_error:
        blocked = create_blocked_report(
            source=source,
            source_type=source_type,
            model=model,
            reason=model_error,
            input_provenance=input_provenance,
        )
        save_report(blocked, report_path)
        return 2, blocked, model_error

    camera_id = "phase4-validation"
    tracker = IoUTracker()
    reader = StreamReader(camera_id, source)
    pipeline = CameraInferencePipeline(camera_id, model, tracker, threading.Lock())
    started = time.monotonic()
    processing_finished: float | None = None
    source_terminal_state = None
    errors: list[str] = []
    reader.start()
    pipeline.start(reader)
    deadline = started + duration_seconds if duration_seconds is not None else None
    try:
        while True:
            reader_thread_alive = bool(reader._thread and reader._thread.is_alive())
            pipeline_thread_alive = bool(pipeline._thread and pipeline._thread.is_alive())
            if not reader_thread_alive and not pipeline_thread_alive:
                break
            if deadline is not None and time.monotonic() >= deadline:
                source_terminal_state = reader.status
                break
            time.sleep(0.025)
    except KeyboardInterrupt:
        source_terminal_state = "INTERRUPTED"
        errors.append("INTERRUPTED")
    finally:
        # Capture the processing window before teardown so the reported
        # duration and processing FPS are not depressed by shutdown time.
        processing_finished = time.monotonic()
        if reader._running:
            source_terminal_state = source_terminal_state or reader.status
            reader.stop()
        if pipeline._thread and pipeline._thread.is_alive():
            pipeline._thread.join(timeout=30.0)
        if pipeline._thread and pipeline._thread.is_alive():
            errors.append("PIPELINE_SHUTDOWN_TIMEOUT")
            pipeline.stop()

    if source_terminal_state is None:
        source_terminal_state = reader.status
    if source_terminal_state in {"SOURCE_UNAVAILABLE", "DISCONNECTED"}:
        errors.append(source_terminal_state)
    state = pipeline.status()
    if reader.total_frames_read == 0:
        errors.append("NO_FRAMES_RECEIVED")
    if state["inferences_completed"] == 0:
        errors.append("NO_INFERENCE_COMPLETED")
    if reader.last_error:
        errors.append(reader.last_error)
    if state["last_error_type"]:
        errors.append(state["last_error_type"])
    elapsed = max((processing_finished or time.monotonic()) - started, 0.0)
    pipeline_ran_cleanly = (
        reader.total_frames_read > 0
        and state["inferences_completed"] > 0
        and state["inference_failures"] == 0
        and state["tracking_failures"] == 0
        and not errors
    )
    if input_provenance == TEST_FIXTURE:
        # Test doubles are never real input. A technically clean fixture run is
        # reported as a test execution, never as real-input validation.
        validation_status = "TEST_FIXTURE_EXECUTION" if pipeline_ran_cleanly else "NOT_VALIDATED"
    else:
        validation_status = "REAL_INPUT_VALIDATED" if pipeline_ran_cleanly else "NOT_VALIDATED"
    report = create_report(
        source=source,
        source_type=source_type,
        model=model,
        reader=reader,
        pipeline=pipeline,
        processing_seconds=elapsed,
        validation_status=validation_status,
        errors=errors,
        stream_terminal_state=source_terminal_state,
        input_provenance=input_provenance,
    )
    output_path = save_report(report, report_path)
    print(f"validation_status: {validation_status}")
    print(f"input_provenance: {input_provenance}")
    print(f"report: {output_path}")
    return (0 if pipeline_ran_cleanly else 1), report, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the existing Phase 4 video-to-tracking pipeline.")
    parser.add_argument("--source", help="Local video or RTSP/IP URL; prefer RTSP_URL env for credentials")
    parser.add_argument("--duration-seconds", type=float, help="Capture duration; local files otherwise run to EOF, streams default to 30 seconds")
    parser.add_argument("--report", help="Output JSON validation report path (must remain access-controlled)")
    args = parser.parse_args(argv)
    if args.duration_seconds is not None and args.duration_seconds <= 0:
        parser.error("--duration-seconds must be positive")

    try:
        source = resolve_source(args.source)
    except ValidationError as exc:
        print(exc.code, file=sys.stderr)
        if not settings.MODEL_WEIGHTS_PATH:
            print("MODEL_NOT_CONFIGURED", file=sys.stderr)
        return 2
    try:
        input_type = validate_source(source)
    except ValidationError as exc:
        print(exc.code, file=sys.stderr)
        return 2

    model = build_model_adapter()
    duration = args.duration_seconds
    if duration is None and input_type in {"RTSP", "IP_STREAM"}:
        duration = 30.0
    code, _, error = run_validation(source, input_type, model, duration, args.report)
    if error:
        print(error, file=sys.stderr)
    return code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(main())
