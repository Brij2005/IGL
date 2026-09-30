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

from ai_models.ultralytics_adapter import UltralyticsModelAdapter
from app.config import settings
from app.services.inference_pipeline import CameraInferencePipeline
from app.services.tracker import IoUTracker
from app.services.video_ingestion import StreamReader
from app.utils.redaction import safe_source_identifier


LOGGER = logging.getLogger("igl.validation")
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}
VALIDATION_STATUSES = {"NOT_VALIDATED", "TECHNICAL_PIPELINE_VALIDATED", "REAL_INPUT_VALIDATED", "IGL_VALIDATED"}


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
    source_identifier: str,
    model: UltralyticsModelAdapter,
    reader: StreamReader | None,
    pipeline: CameraInferencePipeline | None,
    processing_seconds: float,
    validation_status: str,
    errors: list[str],
) -> dict[str, Any]:
    reader_frames = reader.total_frames_read if reader else 0
    pipeline_status = pipeline.status() if pipeline else {}
    processed_frames = int(pipeline_status.get("frames_seen", 0))
    inference_count = model.inference_count
    tracking_metrics = pipeline.tracker.metrics() if pipeline else {"unique_track_count": 0, "lifecycle_transitions": {}}
    processing_fps = round(processed_frames / processing_seconds, 3) if processing_seconds > 0 else None
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
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
            "confidence_threshold": model.confidence_threshold,
            "device": model.device,
            "load_status": model.health()["status"],
            "class_names": model.health()["classes"],
        },
        "input": {
            "source_type": source_type,
            "source_identifier": source_identifier,
            "resolution": list(reader.source_resolution) if reader and reader.source_resolution else None,
            "source_fps": reader.source_fps if reader else None,
            "duration_processed_seconds": round(processing_seconds, 3),
            "source_duration_seconds": reader.source_duration_seconds if reader else None,
            "connection_established": reader._has_connected if reader else False,
            "first_frame_timestamp": reader.first_frame_timestamp.isoformat() if reader and reader.first_frame_timestamp else None,
            "last_frame_timestamp": reader.last_frame_timestamp.isoformat() if reader and reader.last_frame_timestamp else None,
        },
        "pipeline": {
            "frames_received": reader_frames,
            "frames_processed": processed_frames,
            "inference_count": inference_count,
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
    if report["validation_status"] not in VALIDATION_STATUSES:
        raise ValueError("Invalid validation status")
    return report


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
) -> tuple[int, dict[str, Any] | None, str | None]:
    model_error = validate_model_configuration(model)
    if model_error:
        return 2, None, model_error

    camera_id = "phase4-validation"
    tracker = IoUTracker()
    reader = StreamReader(camera_id, source)
    pipeline = CameraInferencePipeline(camera_id, model, tracker, threading.Lock())
    started = time.monotonic()
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
        if reader._running:
            source_terminal_state = source_terminal_state or reader.status
            reader.stop()
        if pipeline._thread and pipeline._thread.is_alive():
            pipeline._thread.join(timeout=30.0)
        if pipeline._thread and pipeline._thread.is_alive():
            errors.append("PIPELINE_SHUTDOWN_TIMEOUT")
            pipeline.stop()

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
    elapsed = max(time.monotonic() - started, 0.0)
    success = (
        reader.total_frames_read > 0
        and state["inferences_completed"] > 0
        and state["inference_failures"] == 0
        and state["tracking_failures"] == 0
        and not errors
    )
    validation_status = "REAL_INPUT_VALIDATED" if success else "NOT_VALIDATED"
    report = create_report(
        source=source,
        source_type=source_type,
        source_identifier=safe_source_identifier(source),
        model=model,
        reader=reader,
        pipeline=pipeline,
        processing_seconds=elapsed,
        validation_status=validation_status,
        errors=errors,
    )
    report["input"]["stream_terminal_state"] = source_terminal_state or reader.status
    output_path = save_report(report, report_path)
    print(f"validation_status: {validation_status}")
    print(f"report: {output_path}")
    return (0 if success else 1), report, None


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
