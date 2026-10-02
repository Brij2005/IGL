"""Validate a configured model artifact without needing a video source.

Every check is reported separately and independently. A check that could not run
is reported as ``NOT_RUN``, never as a pass, so a report can never be read as
though a step succeeded when it was skipped.

The inference probe feeds the model a deterministic synthetic blank frame so the
artifact can be loaded, executed, and timed with no camera present. That probe
is labelled ``SYNTHETIC_PROBE_NOT_REAL_FOOTAGE`` throughout: it demonstrates that
the file loads, that inference executes, and that the returned structure is
well-formed. It says nothing whatsoever about detection quality, and this script
never reports a validation state above ``NOT_VALIDATED``.

Classes, latency, and inference FPS are read only from a model that really
loaded and really ran. Nothing is downloaded here, and a checkpoint whose
runtime is not installed is reported as ``MODEL_RUNTIME_DEPENDENCY_MISSING``
with the missing distribution named.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for import_path in (str(PROJECT_ROOT), str(BACKEND_ROOT)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

import numpy as np

from ai_models.ultralytics_adapter import (
    DISCOVERY_NOTE,
    UltralyticsModelAdapter,
    discovery_permission,
    weights_checksum,
)
from app.config import settings


LOGGER = logging.getLogger("igl.model.validation")

#: Ordered checks. Each is reported on its own line of the summary.
VALIDATION_STEPS = (
    "FILE_EXISTS",
    "FILE_READABLE",
    "MODEL_LOADS",
    "CLASSES_PRESENT",
    "INFERENCE_EXECUTES",
    "OUTPUT_SCHEMA_VALID",
    "LATENCY_MEASURED",
)

PASS = "PASS"
FAIL = "FAIL"
NOT_RUN = "NOT_RUN"

#: Attached to the frame and to every measurement derived from it.
PROBE_LABEL = "SYNTHETIC_PROBE_NOT_REAL_FOOTAGE"
PROBE_NOTE = (
    "A deterministic blank frame is used to exercise load and throughput only. "
    "It contains no plant imagery, so it cannot demonstrate detection accuracy."
)

#: This script can load and time a model. Only a validated study against real
#: plant footage can raise this, and this script is not that.
VALIDATION_STATE = "NOT_VALIDATED"

#: Exit codes. 2 means no check could run at all, which is different from a
#: check that ran and failed.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2


class ValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def record_step(steps: list[dict[str, Any]], step: str, state: str, detail: str | None = None, **values: Any) -> None:
    """Append one check result. Later keys never overwrite the standard ones."""
    entry: dict[str, Any] = {"step": step, "state": state, "detail": detail}
    for key, value in values.items():
        entry.setdefault(key, value)
    steps.append(entry)


def not_run_report(steps: list[dict[str, Any]], reason: str) -> list[dict[str, Any]]:
    """Mark every check that was never reached as NOT_RUN."""
    recorded = {entry["step"] for entry in steps}
    for step in VALIDATION_STEPS:
        if step not in recorded:
            record_step(steps, step, NOT_RUN, reason)
    return steps


def resolve_weights_path(cli_path: str | None = None) -> str:
    """Return the weights path to validate.

    A missing configuration is reported as ``MODEL_NOT_CONFIGURED`` rather than
    being defaulted to some file that may or may not exist.
    """
    if cli_path and cli_path.strip():
        return cli_path.strip()
    configured = settings.MODEL_WEIGHTS_PATH
    if not configured or not configured.strip():
        raise ValidationError("MODEL_NOT_CONFIGURED")
    return configured.strip()


def build_adapter(weights_path: str) -> UltralyticsModelAdapter:
    return UltralyticsModelAdapter(
        weights_path=weights_path,
        model_name=settings.MODEL_NAME,
        model_version=settings.MODEL_VERSION,
        confidence_threshold=settings.MODEL_CONFIDENCE_THRESHOLD,
        device=settings.MODEL_DEVICE,
    )


def synthetic_probe_frame(image_size: int | None = None) -> np.ndarray:
    """Return a deterministic blank frame for a load and throughput probe only.

    The frame is an array of zeros at ``MODEL_IMAGE_SIZE``. It is deliberately
    featureless so a result derived from it is a throughput measurement and not
    a claim about anything visible in the plant.
    """
    side = int(image_size or settings.MODEL_IMAGE_SIZE)
    return np.zeros((side, side, 3), dtype=np.uint8)


def package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "NOT_INSTALLED"


def detection_schema_errors(detections: list[Any]) -> list[str]:
    """Return every reason a returned detection is not well-formed.

    An empty result is not an error: a blank probe frame legitimately yields no
    objects, and that is reported as an empty result rather than a failure.
    """
    errors: list[str] = []
    for index, detection in enumerate(detections):
        confidence = getattr(detection, "confidence", None)
        bbox = getattr(detection, "bbox", None)
        if not isinstance(getattr(detection, "class_name", None), str) or not detection.class_name:
            errors.append(f"detection[{index}].class_name is missing or not a string")
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            errors.append(f"detection[{index}].confidence is not a proportion")
        if not isinstance(bbox, tuple) or len(bbox) != 4:
            errors.append(f"detection[{index}].bbox is not four coordinates")
    return errors


def measure_latency(
    adapter: UltralyticsModelAdapter,
    frame: np.ndarray,
    camera_id: str,
    warmup_passes: int,
    timed_passes: int,
) -> dict[str, Any]:
    """Time real model calls and report the throughput they achieved.

    Warm-up passes are discarded so the reported latency is a steady-state
    measurement rather than a first-call artefact. Every timed pass is an
    executed prediction; nothing here is estimated.
    """
    timestamp = datetime.now(timezone.utc)
    for _ in range(max(int(warmup_passes), 0)):
        adapter.predict(camera_id, timestamp, frame)

    samples_ms: list[float] = []
    completed_passes = 0
    error: str | None = None
    for _ in range(max(int(timed_passes), 1)):
        started = time.perf_counter()
        try:
            adapter.predict(camera_id, timestamp, frame)
        except Exception as exc:  # noqa: BLE001 - any failure ends the measurement
            error = type(exc).__name__
            break
        samples_ms.append(round((time.perf_counter() - started) * 1000.0, 3))
        completed_passes += 1

    if not samples_ms:
        return {
            "completed_passes": 0,
            "requested_passes": int(timed_passes),
            "warmup_passes": int(warmup_passes),
            "latency_ms": None,
            "inference_fps": None,
            "latency_samples_ms": [],
            "error": error or "NO_PASS_COMPLETED",
            "probe_label": PROBE_LABEL,
        }

    mean_ms = round(sum(samples_ms) / len(samples_ms), 3)
    return {
        "completed_passes": completed_passes,
        "requested_passes": int(timed_passes),
        "warmup_passes": int(warmup_passes),
        "latency_ms": mean_ms,
        "median_latency_ms": round(median(samples_ms), 3),
        "min_latency_ms": min(samples_ms),
        "max_latency_ms": max(samples_ms),
        "inference_fps": round(1000.0 / mean_ms, 3) if mean_ms > 0 else None,
        "latency_samples_ms": samples_ms,
        "error": error,
        "probe_label": PROBE_LABEL,
    }


def environment_report() -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "python_version": platform.python_version(),
        "ultralytics_version": package_version("ultralytics"),
        "torch_version": package_version("torch"),
        "onnxruntime_version": package_version("onnxruntime"),
        "tensorrt_version": package_version("tensorrt"),
    }


def run_validation(
    weights_path: str | None = None,
    *,
    report_path: str | Path | None = None,
    warmup_passes: int | None = None,
    timed_passes: int | None = None,
) -> tuple[int, dict[str, Any], str | None]:
    """Validate one model artifact and return ``(exit_code, report, reason)``."""
    warmup = int(settings.MODEL_VALIDATION_WARMUP_PASSES if warmup_passes is None else warmup_passes)
    timed = int(settings.MODEL_VALIDATION_TIMED_PASSES if timed_passes is None else timed_passes)
    steps: list[dict[str, Any]] = []
    probe_camera_id = "model-validation-probe"

    try:
        resolved = resolve_weights_path(weights_path)
    except ValidationError as exc:
        not_run_report(steps, exc.code)
        report = _assemble_report(
            weights_path=None, steps=steps, overall_state=exc.code, adapter=None, measurement=None, probe=None
        )
        return EXIT_BLOCKED, report, exc.code

    candidate = Path(resolved).expanduser()
    probe = None
    measurement = None
    adapter: UltralyticsModelAdapter | None = None

    if not candidate.is_file():
        record_step(steps, "FILE_EXISTS", FAIL, f"No file at {candidate}")
        not_run_report(steps, "FILE_EXISTS_FAILED")
        report = _assemble_report(
            weights_path=str(candidate),
            steps=steps,
            overall_state="MODEL_FILE_NOT_FOUND",
            adapter=None,
            measurement=None,
            probe=None,
        )
        return EXIT_BLOCKED, report, "MODEL_FILE_NOT_FOUND"

    record_step(steps, "FILE_EXISTS", PASS, f"File present at {candidate}", path=str(candidate))

    try:
        with candidate.open("rb") as weights_file:
            first_byte = weights_file.read(1)
        size_bytes = candidate.stat().st_size
    except OSError as exc:
        record_step(steps, "FILE_READABLE", FAIL, f"{type(exc).__name__}: {exc}")
        not_run_report(steps, "FILE_READABLE_FAILED")
        report = _assemble_report(
            weights_path=str(candidate),
            steps=steps,
            overall_state="MODEL_NOT_READABLE",
            adapter=None,
            measurement=None,
            probe=None,
        )
        return EXIT_BLOCKED, report, "MODEL_NOT_READABLE"

    if not first_byte or size_bytes == 0:
        record_step(steps, "FILE_READABLE", FAIL, "File is empty")
        not_run_report(steps, "FILE_READABLE_FAILED")
        report = _assemble_report(
            weights_path=str(candidate),
            steps=steps,
            overall_state="MODEL_NOT_READABLE",
            adapter=None,
            measurement=None,
            probe=None,
        )
        return EXIT_BLOCKED, report, "MODEL_NOT_READABLE"

    record_step(steps, "FILE_READABLE", PASS, f"Readable, {size_bytes} bytes", size_bytes=size_bytes)

    adapter = build_adapter(str(candidate))
    if not adapter.load():
        status = adapter.health()["status"]
        record_step(
            steps,
            "MODEL_LOADS",
            FAIL,
            status,
            load_status=status,
            missing_runtime_packages=list(adapter.missing_runtime_packages),
        )
        not_run_report(steps, f"MODEL_LOADS_FAILED:{status}")
        report = _assemble_report(
            weights_path=str(candidate), steps=steps, overall_state=status, adapter=adapter, measurement=None, probe=None
        )
        return EXIT_FAILED, report, status

    health = adapter.health()
    record_step(
        steps,
        "MODEL_LOADS",
        PASS,
        f"Loaded through {health['framework']}",
        load_status=health["status"],
        framework=health["framework"],
        missing_runtime_packages=[],
    )

    if health["classes"]:
        record_step(steps, "CLASSES_PRESENT", PASS, f"{len(health['classes'])} classes reported by the model",
                    class_count=len(health["classes"]))
    else:
        record_step(steps, "CLASSES_PRESENT", FAIL, "Model reported no class names",
                    class_count=0)
        not_run_report(steps, "CLASSES_PRESENT_FAILED")
        report = _assemble_report(
            weights_path=str(candidate), steps=steps, overall_state="MODEL_CLASSES_MISSING",
            adapter=adapter, measurement=None, probe=None,
        )
        return EXIT_FAILED, report, "MODEL_CLASSES_MISSING"

    frame = synthetic_probe_frame()
    probe = {
        "probe_label": PROBE_LABEL,
        "note": PROBE_NOTE,
        "frame_shape": list(frame.shape),
        "frame_mean": round(float(np.mean(frame)), 6),
    }

    try:
        detections = adapter.predict(probe_camera_id, datetime.now(timezone.utc), frame)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        record_step(steps, "INFERENCE_EXECUTES", FAIL, f"{type(exc).__name__}: {exc}")
        not_run_report(steps, "INFERENCE_EXECUTES_FAILED")
        report = _assemble_report(
            weights_path=str(candidate), steps=steps, overall_state="INFERENCE_DID_NOT_EXECUTE",
            adapter=adapter, measurement=None, probe=probe,
        )
        return EXIT_FAILED, report, "INFERENCE_DID_NOT_EXECUTE"

    record_step(
        steps,
        "INFERENCE_EXECUTES",
        PASS,
        f"Real model call completed, {len(detections)} detections on a synthetic blank frame",
        detections_returned=len(detections),
        probe_label=PROBE_LABEL,
    )

    schema_errors = detection_schema_errors(detections)
    if schema_errors:
        record_step(steps, "OUTPUT_SCHEMA_VALID", FAIL, "; ".join(schema_errors[:5]))
        not_run_report(steps, "OUTPUT_SCHEMA_INVALID")
        report = _assemble_report(
            weights_path=str(candidate), steps=steps, overall_state="MODEL_OUTPUT_SCHEMA_INVALID",
            adapter=adapter, measurement=None, probe=probe,
        )
        return EXIT_FAILED, report, "MODEL_OUTPUT_SCHEMA_INVALID"

    record_step(
        steps,
        "OUTPUT_SCHEMA_VALID",
        PASS,
        "Every returned detection satisfies the detection schema",
        detections_returned=len(detections),
    )

    measurement = measure_latency(adapter, frame, probe_camera_id, warmup, timed)
    if measurement["completed_passes"] < 1 or measurement["latency_ms"] is None:
        record_step(
            steps,
            "LATENCY_MEASURED",
            FAIL,
            f"No timed pass completed: {measurement.get('error') or 'unknown reason'}",
            latency_ms=None,
            inference_fps=None,
            completed_passes=measurement["completed_passes"],
        )
        report = _assemble_report(
            weights_path=str(candidate), steps=steps, overall_state="LATENCY_NOT_MEASURED",
            adapter=adapter, measurement=measurement, probe=probe,
        )
        return EXIT_FAILED, report, "LATENCY_NOT_MEASURED"

    record_step(
        steps,
        "LATENCY_MEASURED",
        PASS,
        (
            f"{measurement['completed_passes']} timed passes, mean {measurement['latency_ms']} ms, "
            f"{measurement['inference_fps']} inference FPS"
        ),
        latency_ms=measurement["latency_ms"],
        inference_fps=measurement["inference_fps"],
        completed_passes=measurement["completed_passes"],
        probe_label=PROBE_LABEL,
    )

    report = _assemble_report(
        weights_path=str(candidate),
        steps=steps,
        overall_state="TECHNICAL_CHECKS_PASSED",
        adapter=adapter,
        measurement=measurement,
        probe=probe,
    )
    return EXIT_OK, report, None


def _assemble_report(
    *,
    weights_path: str | None,
    steps: list[dict[str, Any]],
    overall_state: str,
    adapter: UltralyticsModelAdapter | None,
    measurement: dict[str, Any] | None,
    probe: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the report. Every measurement is passed in, never recomputed."""
    health = adapter.health() if adapter is not None else {}
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_state": overall_state,
        "checks": list(steps),
        "environment": environment_report(),
        "model": {
            "weights_path": weights_path,
            "weights_checksum_sha256": weights_checksum(weights_path) if weights_path else None,
            "framework": health.get("framework"),
            "model_name": health.get("model_name"),
            "model_version": health.get("model_version"),
            "load_status": health.get("status"),
            "missing_runtime_packages": health.get("missing_runtime_packages", []),
            "class_count": len(health.get("classes") or ()),
            "class_names": health.get("classes"),
            "confidence_threshold": health.get("confidence_threshold"),
            "device": health.get("device"),
        },
        "probe": probe,
        "throughput": measurement,
        "discovery": {
            "permission": discovery_permission(),
            "auto_discover_enabled": bool(settings.MODEL_AUTO_DISCOVER),
            "note": DISCOVERY_NOTE,
        },
        # A blank probe frame cannot establish anything about real detections.
        "validation_state": VALIDATION_STATE,
        "detection_quality_validated": False,
        "igl_validated": False,
        "validation_note": (
            "This run proves only that the artifact loads, executes, and returns a well-formed "
            "result under a synthetic probe. Validate against authorized real plant footage "
            "separately; this report does not speak to detection accuracy."
        ),
    }


def save_report(report: dict[str, Any], report_path: str | Path | None = None) -> Path:
    target = Path(report_path) if report_path else (
        PROJECT_ROOT / "data" / "validation_reports" /
        f"model_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)
    return target


def print_summary(report: dict[str, Any], report_path: Path) -> None:
    print(f"overall_state: {report['overall_state']}")
    print(f"validation_state: {report['validation_state']}")
    print(f"probe: {PROBE_LABEL}")
    for entry in report["checks"]:
        print(f"  {entry['step']}: {entry['state']} ({entry['detail']})")
    throughput = report.get("throughput") or {}
    if throughput.get("latency_ms") is not None:
        print(f"latency_ms: {throughput['latency_ms']}")
        print(f"inference_fps: {throughput['inference_fps']}")
    print(f"detection_quality_validated: {report['detection_quality_validated']}")
    print(f"report: {report_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a configured model artifact without a video source."
    )
    parser.add_argument("--weights", help="Weights file to validate; defaults to MODEL_WEIGHTS_PATH")
    parser.add_argument("--report", help="Output JSON validation report path (must remain access-controlled)")
    parser.add_argument("--warmup-passes", type=int, help="Discarded warm-up passes before timing")
    parser.add_argument("--timed-passes", type=int, help="Timed passes used for latency and FPS")
    args = parser.parse_args(argv)
    if args.warmup_passes is not None and args.warmup_passes < 0:
        parser.error("--warmup-passes cannot be negative")
    if args.timed_passes is not None and args.timed_passes < 1:
        parser.error("--timed-passes must be at least 1")

    code, report, reason = run_validation(
        args.weights,
        report_path=args.report,
        warmup_passes=args.warmup_passes,
        timed_passes=args.timed_passes,
    )
    output_path = save_report(report, args.report)
    print_summary(report, output_path)
    if reason:
        print(reason, file=sys.stderr)
    return code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(main())
