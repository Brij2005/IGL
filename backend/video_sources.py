import os
import time
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import cv2
import numpy as np

from config import CAMERA_READ_TIMEOUT_SECONDS


class VideoSourceAdapter(ABC):
    kind = "base"
    requires_backend = True

    def __init__(self, source: str, options: dict = None):
        self.source = source
        self.options = options or {}
        self.capture = None
        self.last_read_at = 0.0
        self.opened_at = 0.0
        self.error = None
        self.open()

    def open(self):
        self._open()
        return self

    @abstractmethod
    def _open(self):
        raise NotImplementedError

    def _read_raw(self):
        if not self.capture or not self.capture.isOpened():
            raise RuntimeError(self.error or "Video source is not open")
        ok, frame = self.capture.read()
        if not ok or frame is None:
            raise RuntimeError("Frame read failed from video source")
        self.last_read_at = time.time()
        return frame

    def read(self):
        deadline = time.time() + CAMERA_READ_TIMEOUT_SECONDS
        last_error = None
        while time.time() < deadline:
            try:
                return self._read_raw()
            except Exception as exc:
                last_error = exc
                time.sleep(0.05)
        raise RuntimeError(str(last_error) if last_error else "Timed out reading video source")

    def release(self):
        try:
            if self.capture:
                self.capture.release()
        except Exception:
            pass
        self.capture = None

    def is_alive(self) -> bool:
        if not self.capture or not self.capture.isOpened():
            return False
        if self.kind == "image":
            return True
        return time.time() - self.last_read_at < max(30.0, CAMERA_READ_TIMEOUT_SECONDS * 4)

    def properties(self) -> dict:
        if not self.capture:
            return {"width": 0, "height": 0, "fps": 0}
        try:
            return {
                "width": int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "fps": round(float(self.capture.get(cv2.CAP_PROP_FPS) or 0), 2),
            }
        except Exception:
            return {"width": 0, "height": 0, "fps": 0}


class WebcamAdapter(VideoSourceAdapter):
    kind = "webcam"

    def _open(self):
        index = int(self.options.get("device_index", self.source or 0))
        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
        if not capture.isOpened():
            raise RuntimeError(f"Webcam {index} could not be opened")
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.options.get("width", 1280)))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.options.get("height", 720)))
        capture.set(cv2.CAP_PROP_FPS, float(self.options.get("fps", 25)))
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        self.capture = capture
        self.opened_at = time.time()
        self._read_raw()


class RTSPAdapter(VideoSourceAdapter):
    kind = "rtsp"

    def _open(self):
        capture = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        try:
            capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self.options.get("timeout_ms", 8000)))
            capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.options.get("timeout_ms", 8000)))
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except Exception:
            pass
        if not capture.isOpened():
            capture.release()
            raise RuntimeError("RTSP camera could not be opened; verify URL, credentials and network reachability")
        self.capture = capture
        self.opened_at = time.time()
        self._read_raw()


class FileAdapter(VideoSourceAdapter):
    kind = "file"

    def _open(self):
        if not os.path.exists(self.source):
            raise RuntimeError(f"Video file not found: {self.source}")
        capture = cv2.VideoCapture(self.source)
        if not capture.isOpened():
            raise RuntimeError("Video file could not be decoded by OpenCV")
        self.capture = capture
        self.opened_at = time.time()
        self._read_raw()

    def read(self):
        ok, frame = self.capture.read() if self.capture else (False, None)
        if not ok or frame is None:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.capture.read()
        if not ok or frame is None:
            raise RuntimeError("Video file ended and could not be looped")
        self.last_read_at = time.time()
        return frame


class ImageAdapter(VideoSourceAdapter):
    kind = "image"
    requires_backend = True

    def _open(self):
        if not os.path.exists(self.source):
            raise RuntimeError(f"Image file not found: {self.source}")
        self.frame = cv2.imread(self.source, cv2.IMREAD_COLOR)
        if self.frame is None:
            raise RuntimeError("Image could not be decoded")
        self.capture = _ImageCapture(self.frame)
        self.opened_at = time.time()
        self.last_read_at = time.time()


class _ImageCapture:
    def __init__(self, frame):
        self.frame = frame

    def isOpened(self):
        return self.frame is not None

    def read(self):
        return True, self.frame.copy()

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.frame.shape[1])
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.frame.shape[0])
        if prop == cv2.CAP_PROP_FPS:
            return 1.0
        return 0.0

    def set(self, prop, value):
        return True

    def release(self):
        self.frame = None


class PublicHTTPAdapter(RTSPAdapter):
    kind = "http"

    def _open(self):
        capture = cv2.VideoCapture(self.source)
        if not capture.isOpened():
            raise RuntimeError("Public HTTP video could not be opened")
        self.capture = capture
        self.opened_at = time.time()
        self._read_raw()


class ONVIFAdapter(RTSPAdapter):
    kind = "onvif"

    def _open(self):
        parsed = urlparse(self.source)
        if parsed.scheme == "onvif":
            host = parsed.hostname or ""
            port = parsed.port or 554
            path = parsed.path or "/onvif/device_service"
            auth = ""
            if parsed.username:
                auth = f"{parsed.username}:{parsed.password or ''}@"
            self.source = f"rtsp://{auth}{host}:{port}{path}"
        super()._open()


class MockAdapter(VideoSourceAdapter):
    """Integration-test adapter only. Never used for production inference."""

    kind = "mock"
    requires_backend = True

    def _open(self):
        color = self.options.get("color", (40, 90, 160))
        size = int(self.options.get("size", 320))
        self.frame = np.full((size, size, 3), color, dtype=np.uint8)
        cv2.putText(self.frame, "IGL MOCK TEST SOURCE", (20, size // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        self.capture = _ImageCapture(self.frame)
        self.opened_at = time.time()
        self.last_read_at = time.time()


def create_adapter(source_type: str, source: str, options: dict = None) -> VideoSourceAdapter:
    normalized = (source_type or "").lower()
    options = options or {}
    if normalized == "webcam":
        return WebcamAdapter(source, options)
    if normalized == "rtsp":
        return RTSPAdapter(source, options)
    if normalized == "onvif":
        return ONVIFAdapter(source, options)
    if normalized in {"file", "video"}:
        return FileAdapter(source, options)
    if normalized in {"image", "img"}:
        return ImageAdapter(source, options)
    if normalized in {"http", "https", "public"}:
        return PublicHTTPAdapter(source, options)
    if normalized == "mock":
        return MockAdapter(source, options)
    raise ValueError(f"Unsupported source_type: {source_type}")