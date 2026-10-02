"""Explicitly configured Ultralytics adapter; never downloads model weights.

Supported checkpoint formats are ``.pt`` (Ultralytics Python), ``.onnx``
(Ultralytics ONNX) and ``.engine`` (Ultralytics TensorRT). A format whose
runtime is not installed on this machine is reported as
``MODEL_RUNTIME_DEPENDENCY_MISSING`` with the missing distribution named.
Nothing is installed and nothing is downloaded to make a load look successful.

Every value in :meth:`UltralyticsModelAdapter.health` is either a configuration
value or a measurement taken from a prediction that actually completed.
``inference_fps`` is ``None`` until at least two predictions have completed,
because a single call has no elapsed interval, and ``validation_state`` is
always ``NOT_VALIDATED``: this module never observes real footage, so it can
never assert that a model is validated.

:func:`discover_local_models` scans operator-configured directories for
checkpoints that already exist on this machine and reports their real size and
SHA-256 digest. Discovery is read-only and bounded by
``MODEL_SEARCH_MAX_DEPTH``. It never downloads weights, never loads a file, and
never activates one: a model runs only when ``MODEL_WEIGHTS_PATH`` names it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from ai_models.detection import Detection

try:
    from app.config import settings
except ImportError:
    from backend.app.config import settings


logger = logging.getLogger("igl.ai.model")

#: Weights suffix to the loading framework reported in health output.
SUPPORTED_FRAMEWORKS: dict[str, str] = {
    ".pt": "ULTRALYTICS_PYTHON",
    ".onnx": "ULTRALYTICS_ONNX",
    ".engine": "ULTRALYTICS_TENSORRT",
}

#: Weights suffix to the distributions that must already be importable. Presence
#: is checked, never installed.
REQUIRED_RUNTIME_PACKAGES: dict[str, tuple[str, ...]] = {
    ".pt": ("ultralytics", "torch"),
    ".onnx": ("ultralytics", "onnxruntime"),
    ".engine": ("ultralytics", "tensorrt"),
}

#: Suffixes that local discovery will report.
DISCOVERY_SUFFIXES: tuple[str, ...] = tuple(SUPPORTED_FRAMEWORKS)

#: Number of completed predictions in the rolling FPS window.
FPS_WINDOW_SIZE = 30

#: This module can load and time a model. It cannot observe real footage, so it
#: never reports a validated model.
VALIDATION_STATE = "NOT_VALIDATED"

#: Attached to every discovered candidate.
DISCOVERY_NOTE = "DISCOVERY_ONLY_NEVER_ACTIVATES_A_MODEL"


def weights_checksum(path: Path | str | None) -> str | None:
    """Return the SHA-256 of the configured weights file, or None.

    A model name, version string, and file name cannot prove which bytes were
    evaluated. The checksum is computed from the actual file, so a report can
    be tied to the exact artifact that produced it. Anything that is not a
    readable local path yields None rather than a fabricated digest.
    """
    if not isinstance(path, (str, Path)):
        return None
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as weights_file:
            for chunk in iter(lambda: weights_file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        logger.error(
            "Weights checksum failed",
            extra={"component": "model_adapter", "error_type": type(exc).__name__},
        )
        return None
    return digest.hexdigest()


def _distribution_available(package: str) -> bool:
    """Return whether a runtime is importable, without importing it.

    A module already present in ``sys.modules`` is available by definition, which
    also covers a caller that has installed a stand-in before loading. Otherwise
    the distribution is looked up on disk, so a slow or heavy runtime is not
    loaded merely to answer this question.
    """
    if sys.modules.get(package) is not None:
        return True
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        return False


def missing_runtime_packages(suffix: str) -> list[str]:
    """Return the distributions a checkpoint format needs that are absent.

    Nothing here is ever installed. The result names exactly what is missing so
    an operator can install it deliberately.
    """
    required = REQUIRED_RUNTIME_PACKAGES.get(str(suffix).lower(), ("ultralytics",))
    return [package for package in required if not _distribution_available(package)]


def _iter_files_at_depth(root: Path, max_depth: int, suffixes: Iterable[str]) -> list[Path]:
    """Yield files directly under ``root`` and up to ``max_depth`` levels below it.

    A file sitting in ``root`` counts as depth 1. The walk never follows symlinked
    directories, so a self-referential or looping link cannot turn a bounded scan
    into an unbounded one.
    """
    wanted = {suffix.lower() for suffix in suffixes}
    found: list[Path] = []
    boundary = max(1, int(max_depth))
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts) + 1
        except ValueError:  # pragma: no cover - walk stayed inside root
            continue
        if depth >= boundary:
            directory_names[:] = []
        for file_name in file_names:
            if Path(file_name).suffix.lower() in wanted:
                found.append(current_path / file_name)
    return found


def discover_local_models(
    search_paths: Sequence[str] | None = None,
    max_depth: int | None = None,
) -> list[dict[str, Any]]:
    """Report candidate checkpoints that already exist on this machine.

    Discovery is read-only and bounded: it reads only the operator-configured
    directories, descends at most ``MODEL_SEARCH_MAX_DEPTH`` levels, and looks
    only for ``.pt``, ``.onnx`` and ``.engine`` files. It never downloads
    weights, never imports a runtime, and never loads or activates a candidate:
    every entry is marked ``NOT_ACTIVATED`` and carries :data:`DISCOVERY_NOTE`.
    Activation requires an explicit ``MODEL_WEIGHTS_PATH``.

    Each entry reports the canonical path, the framework that would load the
    format, the real file size, and the real SHA-256 digest. A digest is None
    when the bytes could not be read; it is never replaced with a placeholder.
    """
    roots = list(search_paths) if search_paths is not None else list(settings.MODEL_SEARCH_PATHS or ())
    depth = int(max_depth if max_depth is not None else settings.MODEL_SEARCH_MAX_DEPTH)
    candidates: dict[str, dict[str, Any]] = {}

    for raw_root in roots:
        text = str(raw_root).strip()
        if not text:
            continue
        root = Path(text).expanduser()
        if not root.is_dir():
            logger.info(
                "Model search path is not a directory",
                extra={"component": "model_adapter", "search_path": text},
            )
            continue
        for path in _iter_files_at_depth(root, depth, DISCOVERY_SUFFIXES):
            try:
                canonical = path.resolve()
            except OSError:  # pragma: no cover - unresolvable path
                continue
            if canonical in candidates:
                continue
            try:
                size_bytes = canonical.stat().st_size
            except OSError:
                size_bytes = None
            try:
                modified = canonical.stat().st_mtime
            except OSError:
                modified = None
            suffix = canonical.suffix.lower()
            candidates[str(canonical)] = {
                "path": str(canonical),
                "filename": canonical.name,
                "suffix": suffix,
                "framework": SUPPORTED_FRAMEWORKS.get(suffix, "UNSUPPORTED"),
                "size_bytes": size_bytes,
                "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(modified)) if modified else None,
                "weights_checksum_sha256": weights_checksum(canonical),
                "readable": weights_checksum(canonical) is not None,
                "activation_state": "NOT_ACTIVATED",
                "discovery_note": DISCOVERY_NOTE,
            }

    return [candidates[key] for key in sorted(candidates)]


def discovery_permission() -> str:
    """State whether the application may consider discovered files at all.

    ``MODEL_AUTO_DISCOVER`` only permits an operator tool to *look* at
    operator-supplied directories. Even when it is enabled no discovered file is
    activated, so a scanned directory can never silently change which model the
    platform runs.
    """
    if not settings.MODEL_AUTO_DISCOVER:
        return "MODEL_AUTO_DISCOVERY_DISABLED"
    return "MODEL_AUTO_DISCOVERY_ENABLED_BUT_NO_FILE_IS_ACTIVATED_AUTOMATICALLY"


class UltralyticsModelAdapter:
    def __init__(
        self,
        weights_path: str | None,
        model_name: str | None = None,
        model_version: str | None = None,
        confidence_threshold: float = 0.25,
        device: str = "cpu",
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self.weights_path = Path(weights_path).expanduser() if weights_path else None
        self.model_name = model_name.strip() if model_name and model_name.strip() else "UNCONFIGURED"
        self.model_version = model_version or "UNSPECIFIED"
        self.confidence_threshold = confidence_threshold
        self.device = device
        self.classes: tuple[str, ...] = ()
        self.inference_latency_ms: float | None = None
        self.inference_count = 0
        self.inference_attempt_count = 0
        self.inference_failure_count = 0
        self._latency_total_ms = 0.0
        self.framework = (
            SUPPORTED_FRAMEWORKS.get(self.weights_path.suffix.lower(), "UNSUPPORTED_FORMAT")
            if self.weights_path is not None
            else "UNCONFIGURED"
        )
        self.missing_runtime_packages: list[str] = []
        self.status = "MODEL_NOT_CONFIGURED" if self.weights_path is None else "NOT_LOADED"
        self._model: Any = None
        # Completion times of predictions that really returned. FPS is derived
        # from this window and is None while it holds fewer than two entries.
        self._completed_predictions: deque[float] = deque(maxlen=FPS_WINDOW_SIZE)

    def load(self) -> bool:
        if self.weights_path is None:
            self.status = "MODEL_NOT_CONFIGURED"
            return False
        suffix = self.weights_path.suffix.lower()
        if suffix not in SUPPORTED_FRAMEWORKS:
            self.status = "MODEL_UNSUPPORTED_FORMAT"
            self.framework = "UNSUPPORTED_FORMAT"
            return False
        if not self.weights_path.is_file():
            self.status = "MODEL_INVALID_WEIGHTS"
            return False
        try:
            if self.weights_path.stat().st_size == 0:
                self.status = "MODEL_INVALID_WEIGHTS"
                return False
            with self.weights_path.open("rb") as weights_file:
                if not weights_file.read(1):
                    self.status = "MODEL_INVALID_WEIGHTS"
                    return False
        except OSError:
            self.status = "MODEL_INVALID_WEIGHTS"
            return False
        if self.model_name == "UNCONFIGURED":
            self.status = "MODEL_NAME_REQUIRED"
            return False
        if self.model_version == "UNSPECIFIED":
            self.status = "MODEL_VERSION_REQUIRED"
            return False
        self.framework = SUPPORTED_FRAMEWORKS[suffix]
        self.missing_runtime_packages = missing_runtime_packages(suffix)
        if self.missing_runtime_packages:
            # The file is fine; this machine simply cannot execute the format.
            # Naming the packages is the whole point: nothing is installed here.
            self.status = "MODEL_RUNTIME_DEPENDENCY_MISSING"
            logger.error(
                "Model runtime dependency is missing",
                extra={
                    "component": "model_adapter",
                    "framework": self.framework,
                    "missing_packages": list(self.missing_runtime_packages),
                },
            )
            return False
        try:
            from ultralytics import YOLO

            self._model = YOLO(str(self.weights_path))
            names = self._model.names
            self.classes = tuple(str(names[key]) for key in sorted(names)) if isinstance(names, dict) else tuple(names)
            self.status = "READY"
            return True
        except Exception as exc:
            self._model = None
            self.status = "MODEL_LOAD_FAILED"
            logger.error("Model load failed", extra={"component": "model_adapter", "error_type": type(exc).__name__})
            return False

    def predict(self, camera_id: str, timestamp, frame: np.ndarray) -> list[Detection]:
        if self._model is None:
            raise RuntimeError(f"Model is unavailable: {self.status}")
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("Cannot run inference on an empty frame")
        started = time.perf_counter()
        self.inference_attempt_count += 1
        try:
            results = self._model.predict(frame, conf=self.confidence_threshold, device=self.device, verbose=False)
            detections: list[Detection] = []
            for result in results:
                boxes = getattr(result, "boxes", None)
                if boxes is None:
                    continue
                names = getattr(result, "names", self.classes)
                for box in boxes:
                    class_id = int(box.cls[0].item())
                    class_name = names[class_id] if not isinstance(names, dict) else names[class_id]
                    coordinates = tuple(float(value) for value in box.xyxy[0].tolist())
                    detections.append(Detection(
                        camera_id=camera_id,
                        timestamp=timestamp,
                        frame_timestamp=timestamp,
                        class_name=str(class_name),
                        confidence=float(box.conf[0].item()),
                        bbox=coordinates,
                        model_name=self.model_name,
                        model_version=self.model_version,
                    ))
            self.inference_count += 1
            # Only a prediction that completed contributes a sample.
            self._completed_predictions.append(time.perf_counter())
            return detections
        except Exception as exc:
            self.inference_failure_count += 1
            self.status = "INFERENCE_ERROR"
            logger.error("Inference failed", extra={"component": "model_adapter", "camera_id": camera_id, "error_type": type(exc).__name__})
            raise RuntimeError("Model inference failed") from exc
        finally:
            self.inference_latency_ms = round((time.perf_counter() - started) * 1000, 3)
            self._latency_total_ms += self.inference_latency_ms

    def measured_inference_fps(self) -> float | None:
        """Return inference FPS measured from completed predictions.

        Two completed predictions are the minimum: with fewer, no elapsed
        interval has been observed, and a rate computed from a single call would
        be an invention rather than a measurement. Before that the value is
        None, never zero.
        """
        if len(self._completed_predictions) < 2:
            return None
        elapsed = self._completed_predictions[-1] - self._completed_predictions[0]
        if elapsed <= 0:
            return None
        return round((len(self._completed_predictions) - 1) / elapsed, 3)

    def health(self) -> dict[str, Any]:
        weights_loaded = self._model is not None
        return {
            "framework": self.framework,
            "weights_path": str(self.weights_path) if self.weights_path else None,
            "status": self.status,
            "available": weights_loaded,
            "weights_loaded": weights_loaded,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "weights_checksum_sha256": weights_checksum(self.weights_path),
            "classes": list(self.classes),
            "confidence_threshold": self.confidence_threshold,
            "device": self.device,
            "inference_fps": self.measured_inference_fps(),
            "inference_fps_samples": len(self._completed_predictions),
            "latency_ms": self.inference_latency_ms,
            "inference_latency_ms": self.inference_latency_ms,
            "average_inference_latency_ms": round(self._latency_total_ms / self.inference_attempt_count, 3) if self.inference_attempt_count else None,
            "inference_count": self.inference_count,
            "inference_attempt_count": self.inference_attempt_count,
            "inference_failure_count": self.inference_failure_count,
            "missing_runtime_packages": list(self.missing_runtime_packages),
            # Loading and timing a model is not validating it. This module never
            # sees real footage, so it can never raise this above NOT_VALIDATED.
            "validation_state": VALIDATION_STATE,
        }

    def metadata(self) -> dict[str, Any]:
        return self.health()
