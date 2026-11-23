import json
import os
from pathlib import Path
from threading import RLock

from config import BACKEND_DIR, MODEL_DIR
from database import get_db_connection, now_iso


class InferenceService:
    def __init__(self):
        self._models = {}
        self._failed_at = {}
        self._failure_ttl_seconds = 30
        self._lock = RLock()

    def _resolve_weights(self, weights_path: str) -> str:
        if not weights_path:
            raise FileNotFoundError("Model weights path is not configured")
        path = Path(weights_path)
        if not path.is_absolute():
            candidates = [MODEL_DIR / path, BACKEND_DIR / path, Path.cwd() / path]
            path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
        if not path.exists():
            raise FileNotFoundError(f"Model weights not found: {path}")
        return str(path)

    def get_model(self, model_id: str):
        import time

        with self._lock:
            if model_id in self._models:
                return self._models[model_id]
            failed_at = self._failed_at.get(model_id)
            if failed_at and time.time() - failed_at < self._failure_ttl_seconds:
                return None
            conn = get_db_connection()
            row = conn.execute("SELECT * FROM ai_models WHERE id = ?", (model_id,)).fetchone()
            conn.close()
            if not row:
                self._failed_at[model_id] = time.time()
                return None
            if row["status"] != "AVAILABLE" or not row["weights_path"]:
                self._failed_at[model_id] = time.time()
                return None
            try:
                from ultralytics import YOLO

                weights = self._resolve_weights(row["weights_path"])
                model = YOLO(weights)
                self._models[model_id] = model
                self._failed_at.pop(model_id, None)
                return model
            except Exception as exc:
                print(f"[INFERENCE] Failed to load model {model_id}: {exc}")
                self._failed_at[model_id] = time.time()
                return None

    def invalidate_model(self, model_id: str) -> None:
        with self._lock:
            self._models.pop(model_id, None)
            self._failed_at.pop(model_id, None)

    def reload_model(self, model_id: str) -> dict:
        self.invalidate_model(model_id)
        model = self.get_model(model_id)
        return self.model_status(model_id)

    def model_status(self, model_id: str) -> dict:
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM ai_models WHERE id = ?", (model_id,)).fetchone()
        conn.close()
        if not row:
            return {"status": "NOT_FOUND", "model_id": model_id}
        loaded = self.get_model(model_id)
        classes = {}
        try:
            classes = json.loads(row["classes_json"] or "{}")
        except Exception:
            classes = {}
        return {
            "model_id": model_id,
            "name": row["name"],
            "status": row["status"],
            "loaded": bool(loaded),
            "weights_path": row["weights_path"],
            "classes": classes,
            "task_type": row["task_type"],
        }

    def weights_path(self, model_id: str) -> str:
        conn = get_db_connection()
        row = conn.execute("SELECT weights_path FROM ai_models WHERE id = ?", (model_id,)).fetchone()
        conn.close()
        if not row or not row["weights_path"]:
            raise FileNotFoundError("Model weights are not configured")
        return self._resolve_weights(row["weights_path"])

    def detect_image(self, image_bytes: bytes, model_id: str = "model-yolov8n", conf: float = 0.25, imgsz: int = 1280):
        import cv2
        import numpy as np

        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("image_base64 does not contain a supported image")
        model = self.get_model(model_id)
        if model is None:
            return {
                "status": "MODEL_UNAVAILABLE",
                "message": "The requested model is not trained, configured, or deployable.",
                "model_id": model_id,
                "detections": [],
            }
        try:
            results = model.predict(source=image, conf=conf, imgsz=imgsz, save=False, verbose=False)
        except Exception as exc:
            raise RuntimeError(f"Inference failed: {exc}") from exc
        result = results[0]
        height, width = image.shape[:2]
        detections = []
        if result.boxes is not None:
            for index, box in enumerate(result.boxes):
                class_id = int(box.cls[0].item())
                class_name = model.names.get(class_id, str(class_id))
                confidence = float(box.conf[0].item())
                cx, cy, bw, bh = box.xywh[0].tolist()
                norm_x = max(0.0, min(1.0, (cx - bw / 2.0) / width))
                norm_y = max(0.0, min(1.0, (cy - bh / 2.0) / height))
                norm_w = max(0.0, min(1.0 - norm_x, bw / width))
                norm_h = max(0.0, min(1.0 - norm_y, bh / height))
                detections.append(
                    {
                        "idx": index,
                        "class_id": class_id,
                        "class_name": class_name,
                        "confidence": round(confidence, 4),
                        "bbox": [round(norm_x, 4), round(norm_y, 4), round(norm_w, 4), round(norm_h, 4)],
                        "pixel_bbox": [round(cx - bw / 2, 1), round(cy - bh / 2, 1), round(bw, 1), round(bh, 1)],
                    }
                )
        return {
            "status": "SUCCESS",
            "model_id": model_id,
            "model_path": getattr(model, "ckpt_path", ""),
            "detections_count": len(detections),
            "detections": detections,
        }

    def health(self) -> list:
        conn = get_db_connection()
        rows = conn.execute("SELECT id, name, status, weights_path, task_type FROM ai_models ORDER BY name").fetchall()
        conn.close()
        output = []
        for row in rows:
            model = self.get_model(row["id"])
            output.append(
                {
                    "model_id": row["id"],
                    "name": row["name"],
                    "status": row["status"],
                    "loaded": bool(model),
                    "task_type": row["task_type"],
                    "weights_path": row["weights_path"],
                }
            )
        return output


inference_service = InferenceService()