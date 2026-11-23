import base64
import json
import os
import time
from threading import RLock
from urllib.parse import urlparse, urlunparse

import cv2
import numpy as np

from config import CAMERA_RECONNECT_MAX_ATTEMPTS
from database import get_db_connection, now_iso
from video_sources import create_adapter
from camera_worker import camera_worker_manager


def _frame_to_jpeg(frame, quality=82):
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("Frame could not be encoded as JPEG")
    return buffer.tobytes()


def _safe_source_url(source_url: str) -> str:
    parsed = urlparse(source_url or "")
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        netloc = host
        if parsed.port:
            netloc = f"{host}:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return source_url or ""


def _build_source_url(source_url: str, username: str, password: str) -> str:
    parsed = urlparse(source_url or "")
    if not (username or password):
        return source_url or ""
    host = parsed.hostname or ""
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    userinfo = ""
    if username or password:
        userinfo = f"{username}:{password}@"
    return urlunparse((parsed.scheme, f"{userinfo}{netloc}", parsed.path, parsed.params, parsed.query, parsed.fragment))


class CameraManager:
    def __init__(self):
        self._adapters = {}
        self._lock = RLock()
        self._reconnect_counts = {}

    def _get_camera(self, camera_id: str):
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def _options_for(self, camera: dict) -> dict:
        config = {}
        try:
            config = json.loads(camera.get("config_json") or "{}")
        except Exception:
            config = {}
        return {
            "width": config.get("width", 1920),
            "height": config.get("height", 1080),
            "fps": config.get("fps", 25),
            "timeout_ms": camera.get("connection_timeout_ms") or config.get("timeout_ms", 8000),
            "device_index": config.get("device_index", 0),
        }

    def _start(self, camera: dict) -> None:
        camera_id = camera["id"]
        source_type = (camera.get("source_type") or "").lower()
        source_url = _safe_source_url(camera.get("source_url") or "")
        username = ""
        password = ""
        try:
            credentials = json.loads(camera.get("credentials_json") or "{}")
            from security import decrypt_secret

            username = decrypt_secret(credentials.get("username", ""))
            password = decrypt_secret(credentials.get("password", ""))
        except Exception:
            username = ""
            password = ""
        resolved_url = _build_source_url(source_url, username, password)
        adapter = create_adapter(source_type, resolved_url, self._options_for(camera))
        self._adapters[camera_id] = adapter
        self._reconnect_counts[camera_id] = 0
        self._touch(camera_id, "ONLINE")
        # Start the AI inference worker so live frames genuinely produce events.
        try:
            camera_worker_manager.ensure(camera_id, camera, adapter)
        except Exception as exc:
            self._health_log(camera_id, "WORKER_START_FAILED", str(exc))

    def _touch(self, camera_id: str, status: str, reason: str = None) -> None:
        conn = get_db_connection()
        conn.execute(
            "UPDATE cameras SET status = ?, not_assessable_reason = ?, last_seen = ? WHERE id = ?",
            (status, reason, now_iso(), camera_id),
        )
        conn.commit()
        conn.close()

    def _health_log(self, camera_id: str, event_type: str, details: str = "") -> None:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO health_logs (camera_id, event_type, details) VALUES (?, ?, ?)",
            (camera_id, event_type, details),
        )
        conn.commit()
        conn.close()

    def start(self, camera_id: str):
        with self._lock:
            camera = self._get_camera(camera_id)
            if not camera:
                raise ValueError("Camera not found")
            self.stop(camera_id)
            self._start(camera)
            return True

    def stop(self, camera_id: str):
        with self._lock:
            adapter = self._adapters.pop(camera_id, None)
            if adapter:
                adapter.release()
            self._reconnect_counts.pop(camera_id, None)
            try:
                camera_worker_manager.stop(camera_id)
            except Exception:
                pass
            self._touch(camera_id, "DISCONNECTED")
            self._health_log(camera_id, "STREAM_STOPPED", "Camera stream stopped by operator")

    def stop_all(self):
        with self._lock:
            for camera_id in list(self._adapters.keys()):
                adapter = self._adapters.pop(camera_id, None)
                if adapter:
                    adapter.release()
            try:
                camera_worker_manager.stop_all()
            except Exception:
                pass

    def _reconnect(self, camera_id: str, camera: dict) -> None:
        max_attempts = int(camera.get("reconnect_max_attempts") or CAMERA_RECONNECT_MAX_ATTEMPTS)
        attempts = self._reconnect_counts.get(camera_id, 0)
        if not camera.get("auto_reconnect", 1) or attempts >= max_attempts:
            self._touch(camera_id, "OFFLINE", "Maximum reconnect attempts reached")
            self._health_log(camera_id, "DISCONNECT", "Maximum reconnect attempts reached")
            raise RuntimeError("Camera remains disconnected")
        self._reconnect_counts[camera_id] = attempts + 1
        self._touch(camera_id, "RECONNECTING", f"Reconnect attempt {attempts + 1}/{max_attempts}")
        self._health_log(camera_id, "STREAM_RECOVERY", f"Reconnect attempt {attempts + 1}")
        time.sleep(min(2.0, 0.25 * (attempts + 1)))
        self._adapters.pop(camera_id, None)
        self._start(camera)

    def get_frame(self, camera_id: str):
        with self._lock:
            camera = self._get_camera(camera_id)
            if not camera:
                raise ValueError("Camera not found")
            if not camera.get("enabled", 1):
                raise RuntimeError("Camera is disabled")
            adapter = self._adapters.get(camera_id)
            if adapter is None or not adapter.is_alive():
                self._reconnect(camera_id, camera)
                adapter = self._adapters.get(camera_id)
            try:
                frame = adapter.read()
                return frame
            except Exception:
                self._reconnect(camera_id, camera)
                adapter = self._adapters.get(camera_id)
                return adapter.read()

    def get_frame_and_jpeg(self, camera_id: str, quality: int = 82):
        frame = self.get_frame(camera_id)
        return frame, _frame_to_jpeg(frame, quality)

    def health(self, camera_id: str) -> dict:
        adapter = self._adapters.get(camera_id)
        camera = self._get_camera(camera_id)
        return {
            "camera_id": camera_id,
            "running": bool(adapter and adapter.is_alive()),
            "reconnect_attempts": self._reconnect_counts.get(camera_id, 0),
            "properties": adapter.properties() if adapter else {"width": 0, "height": 0, "fps": 0},
            "last_seen": camera.get("last_seen") if camera else None,
            "status": camera.get("status") if camera else "UNKNOWN",
        }

    def test_source(self, source_type: str, source_url: str, options: dict = None):
        adapter = None
        try:
            adapter = create_adapter(source_type, source_url, options or {})
            frame = adapter.read()
            ok, buffer = cv2.imencode(".jpg", frame)
            preview = base64.b64encode(buffer.tobytes()).decode("ascii") if ok else ""
            return {
                "success": True,
                "kind": adapter.kind,
                "properties": adapter.properties(),
                "preview_base64": preview,
            }
        except Exception as exc:
            return {"success": False, "message": str(exc)}
        finally:
            if adapter:
                adapter.release()


camera_manager = CameraManager()