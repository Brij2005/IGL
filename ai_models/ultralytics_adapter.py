"""Explicitly configured Ultralytics adapter; never downloads model weights."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from ai_models.detection import Detection


logger = logging.getLogger("igl.ai.model")


class UltralyticsModelAdapter:
    def __init__(
        self,
        weights_path: str | None,
        model_name: str | None = None,
        model_version: str | None = None,
        confidence_threshold: float = 0.25,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self.weights_path = Path(weights_path).expanduser() if weights_path else None
        self.model_name = model_name or (self.weights_path.stem if self.weights_path else "UNCONFIGURED")
        self.model_version = model_version or "UNSPECIFIED"
        self.confidence_threshold = confidence_threshold
        self.classes: tuple[str, ...] = ()
        self.inference_latency_ms: float | None = None
        self.inference_count = 0
        self._latency_total_ms = 0.0
        self.status = "MODEL_NOT_CONFIGURED" if self.weights_path is None else "NOT_LOADED"
        self._model: Any = None

    def load(self) -> bool:
        if self.weights_path is None:
            self.status = "MODEL_NOT_CONFIGURED"
            return False
        if self.weights_path.suffix.lower() != ".pt" or not self.weights_path.is_file():
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
        if self.model_version == "UNSPECIFIED":
            self.status = "MODEL_VERSION_REQUIRED"
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
        try:
            results = self._model.predict(frame, conf=self.confidence_threshold, verbose=False)
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
            return detections
        except Exception as exc:
            self.status = "INFERENCE_ERROR"
            logger.error("Inference failed", extra={"component": "model_adapter", "camera_id": camera_id, "error_type": type(exc).__name__})
            raise RuntimeError("Model inference failed") from exc
        finally:
            self.inference_latency_ms = round((time.perf_counter() - started) * 1000, 3)
            self._latency_total_ms += self.inference_latency_ms
            self.inference_count += 1

    def health(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "available": self._model is not None,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "classes": list(self.classes),
            "confidence_threshold": self.confidence_threshold,
            "inference_latency_ms": self.inference_latency_ms,
            "average_inference_latency_ms": round(self._latency_total_ms / self.inference_count, 3) if self.inference_count else None,
            "inference_count": self.inference_count,
        }

    def metadata(self) -> dict[str, Any]:
        return self.health()