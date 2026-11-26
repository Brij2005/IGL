"""Background camera inference worker for IGL VisionGuard.

Connects the live camera capture path to the AI inference pipeline so that a
running camera genuinely produces detection events, incidents, alerts and
evidence — the missing link identified in the codebase audit.

Design:
    * One daemon thread per running camera.
    * Reuses the already-open adapter from `camera_manager`.
    * Runs the camera's configured model via `inference_service` and feeds
      detections to `event_engine.process_image(temporal=True)`.
    * Thermal cameras use the thermal model; RGB cameras use configured models.
      RGB COCO weights are never used on thermal footage.
    * Honest behavior when a model is unavailable: logs and skips, never fakes.
    * Clean shutdown via per-camera stop events and app shutdown.

This module does not add new endpoints; it is wired into `camera_manager`
start/stop and FastAPI startup/shutdown.
"""

import json
import threading
import time

from database import get_db_connection


class CameraWorker:
    """Runs continuous inference for a single camera in a background thread."""

    def __init__(self, camera_id, camera, adapter, inference_interval=1.0):
        self.camera_id = camera_id
        self.camera = camera
        self.adapter = adapter
        self.inference_interval = max(0.2, float(inference_interval))
        self._stop = threading.Event()
        self._thread = None
        self.last_error = None
        self.frames_processed = 0
        self.last_infer_at = 0.0
        # Live scene state surfaced to the UI (Camera Command Centre).
        self.current_scene = "GENERAL"
        self.scene_confidence = 0.0
        self.scene_reason = "Scene not yet analyzed."
        self.last_frame_at = 0.0
        self.active_model_ids = []
        self.scene_switch_count = 0
        self._last_scene = "GENERAL"

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name=f"camworker-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self, join_timeout=3.0):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def _model_ids(self):
        """Resolve which model(s) to run for this camera.

        Delegates to the central mode manager (Automatic scene-based selection
        or Manual operator selection). The manager enforces the hard safety
        rules: unavailable/untrained models are never run and thermal cameras
        are never paired with RGB models.

        When the mode manager is unavailable (e.g. a unit test that builds a
        bare worker), falls back to the camera's configured model ids and then
        the general safety model.
        """
        try:
            from mode_manager import mode_manager

            return mode_manager.model_ids_for(
                self.camera,
                self.current_scene,
                self.scene_confidence,
            )
        except Exception:
            source_type = (self.camera.get("source_type") or "").lower()
            if source_type in {"thermal", "thermal_rtsp"}:
                return ["model-thermal-person"]
            try:
                config = json.loads(self.camera.get("config_json") or "{}")
            except Exception:
                config = {}
            model_ids = config.get("model_ids") or []
            if isinstance(model_ids, str):
                model_ids = [model_ids]
            return model_ids or ["model-yolov8n"]

    def _thresholds(self):
        import json

        try:
            config = json.loads(self.camera.get("config_json") or "{}")
        except Exception:
            config = {}
        thresholds = config.get("thresholds", {}) if isinstance(config.get("thresholds"), dict) else {}
        return {
            "min_confidence": float(thresholds.get("min_confidence", 0.35)),
            "min_frames": int(thresholds.get("min_frames", 2)),
            "cooldown_seconds": int(thresholds.get("cooldown_seconds", 60)),
        }

    def _run(self):
        from event_engine import event_engine

        while not self._stop.is_set():
            now = time.time()
            if now - self.last_infer_at < self.inference_interval:
                time.sleep(0.05)
                continue
            self.last_infer_at = now
            try:
                frame = self.adapter.read()
            except Exception as exc:
                # Camera read failed; let camera_manager reconnect on next preview.
                self.last_error = f"frame read failed: {exc}"
                time.sleep(1.0)
                continue
            self.frames_processed += 1
            self.last_frame_at = now
            try:
                import cv2

                ok, buffer = cv2.imencode(".jpg", frame)
                if not ok:
                    continue
                image_bytes = buffer.tobytes()
            except Exception as exc:
                self.last_error = f"encode failed: {exc}"
                continue

            # Scene-based model selection (Automatic mode) or operator selection
            # (Manual mode) is decided centrally by the mode manager. The scene
            # is derived from the actual frame content, never the camera name.
            try:
                from scene_analyzer import analyze_frame

                scene = analyze_frame(image_bytes, self.camera.get("source_type"))
                self.current_scene = scene.get("scene", self.current_scene)
                self.scene_confidence = scene.get("confidence", 0.0)
                self.scene_reason = scene.get("reason", "")
                if self.current_scene != self._last_scene:
                    self.scene_switch_count += 1
                    self._last_scene = self.current_scene
            except Exception as exc:
                self.last_error = f"scene analysis failed: {exc}"

            model_ids = self._model_ids()
            self.active_model_ids = model_ids
            for model_id in model_ids:
                try:
                    event_engine.process_image(
                        self.camera_id,
                        image_bytes,
                        model_id=model_id,
                        conf=self._thresholds()["min_confidence"],
                        temporal=True,
                    )
                except Exception as exc:
                    self.last_error = f"inference failed ({model_id}): {exc}"
                    # Do not fabricate; just record and continue.

    def status(self):
        return {
            "camera_id": self.camera_id,
            "running": self.is_alive(),
            "frames_processed": self.frames_processed,
            "inference_interval": self.inference_interval,
            "model_ids": self._model_ids(),
            "active_model_ids": self.active_model_ids,
            "scene": self.current_scene,
            "scene_confidence": self.scene_confidence,
            "scene_reason": self.scene_reason,
            "scene_switch_count": self.scene_switch_count,
            "last_frame_at": self.last_frame_at,
            "last_error": self.last_error,
        }


class CameraWorkerManager:
    """Tracks one worker per running camera and owns their lifecycle."""

    def __init__(self):
        self._workers = {}
        self._lock = threading.RLock()

    def ensure(self, camera_id, camera, adapter, inference_interval=1.0):
        with self._lock:
            worker = self._workers.get(camera_id)
            if worker and worker.is_alive():
                return worker
            worker = CameraWorker(camera_id, camera, adapter, inference_interval)
            worker.start()
            self._workers[camera_id] = worker
            return worker

    def stop(self, camera_id):
        with self._lock:
            worker = self._workers.pop(camera_id, None)
        if worker:
            worker.stop()

    def stop_all(self):
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.stop()

    def status(self):
        with self._lock:
            return [w.status() for w in self._workers.values()]

    def is_running(self, camera_id):
        with self._lock:
            worker = self._workers.get(camera_id)
            return bool(worker and worker.is_alive())


camera_worker_manager = CameraWorkerManager()