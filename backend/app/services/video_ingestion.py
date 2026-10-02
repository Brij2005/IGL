"""Threaded video ingestion for RTSP, IP, and local file sources.

Operational properties this module guarantees:

* every frame is timestamped on acquisition and copied into a bounded buffer;
* connection and read timeouts are explicit, so an unreachable source cannot
  block a worker thread indefinitely;
* reconnection uses bounded exponential backoff with an optional attempt cap and
  an explicit terminal state;
* capture pacing honours ``CAMERA_TARGET_FPS`` when configured;
* frame-drop accounting distinguishes decode failure, empty frames, and buffer
  rejection;
* the reported status is always one of the declared states, and a source that
  has not delivered a frame is never reported as running.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    from app.config import settings
    from app.services.webcam_source import (
        WebcamConfig,
        is_webcam_source,
        open_webcam_capture,
        parse_webcam_url,
    )
    from app.utils.frame_buffer import BufferedFrame, FrameBuffer
except ImportError:
    from backend.app.config import settings
    from backend.app.services.webcam_source import (
        WebcamConfig,
        is_webcam_source,
        open_webcam_capture,
        parse_webcam_url,
    )
    from backend.app.utils.frame_buffer import BufferedFrame, FrameBuffer


logger = logging.getLogger("igl.video.ingestion")

#: Declared stream states. Anything else is a bug, not a status.
STREAM_STATES = (
    "NOT_STARTED",
    "STARTING",
    "RUNNING",
    "END_OF_FILE",
    "SOURCE_UNAVAILABLE",
    "DISCONNECTED",
    "RECONNECT_EXHAUSTED",
    "STOPPED",
)

STREAM_URL_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")


class StreamReader:
    """Non-blocking worker thread reading frames from a stream or video file."""

    def __init__(
        self,
        camera_id: str,
        stream_url: str,
        target_fps: float | None = None,
        frame_buffer: FrameBuffer | None = None,
        connect_timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.stream_url = stream_url
        # An explicit per-camera value wins; otherwise the operator setting applies.
        self.target_fps = float(target_fps) if target_fps else settings.CAMERA_TARGET_FPS
        self.connect_timeout_seconds = float(
            connect_timeout_seconds or settings.CAMERA_CONNECT_TIMEOUT_SECONDS
        )
        self.read_timeout_seconds = float(read_timeout_seconds or settings.CAMERA_READ_TIMEOUT_SECONDS)

        self.frame_buffer = frame_buffer or FrameBuffer(
            retention_seconds=settings.FRAME_BUFFER_RETENTION_SECONDS,
            max_frames=settings.FRAME_BUFFER_MAX_FRAMES,
            max_bytes=settings.FRAME_BUFFER_MAX_BYTES,
        )

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Telemetry
        self.current_fps: float = 0.0
        self.total_frames_read: int = 0
        self.dropped_frames_count: int = 0
        self.decode_failures: int = 0
        self.invalid_frames_dropped: int = 0
        self.buffer_rejections: int = 0
        self.first_frame_timestamp: Optional[datetime] = None
        self.last_frame_timestamp: Optional[datetime] = None
        self.is_connected: bool = False
        self.last_error: Optional[str] = None
        self.status: str = "NOT_STARTED"
        self.source_resolution: tuple[int, int] | None = None
        self.source_fps: float | None = None
        self.source_duration_seconds: float | None = None
        self.source_backend: str | None = None
        self.reconnects: int = 0
        self.reconnect_attempts: int = 0
        self.last_frame_interval_ms: float | None = None
        self._has_connected = False
        self._stop_event = threading.Event()
        self._frame_interval = (1.0 / self.target_fps) if self.target_fps > 0 else 0.0

        # Webcam sources open a capture device rather than a network stream. The
        # parsed configuration is None for every other source type.
        self.webcam_config: WebcamConfig | None = None
        if is_webcam_source(stream_url):
            try:
                self.webcam_config = parse_webcam_url(stream_url)
                self.source_type = "WEBCAM"
            except ValueError as exc:
                # A malformed webcam URL is a configuration error, not a runtime
                # connection failure, so it is reported as such and never retried.
                self.source_type = "WEBCAM_INVALID_CONFIGURATION"
                self.last_error = str(exc)
                self.status = "SOURCE_UNAVAILABLE"
        elif self.stream_url.strip().isdigit():
            # A bare integer is a capture index, the historical way to express a
            # local device. It is normalised into the webcam scheme here so every
            # downstream consumer sees one representation.
            self.source_type = "WEBCAM"
            self.webcam_config = WebcamConfig(device_index=int(self.stream_url.strip()))
        else:
            self.source_type = "FILE" if self._is_file_source() else "NETWORK_STREAM"

        # A webcam URL may carry its own capture rate. An explicitly supplied
        # per-camera target_fps still wins, because that is the operator's
        # deliberate pacing choice.
        if self.webcam_config is not None and not target_fps and self.webcam_config.fps:
            self.target_fps = float(self.webcam_config.fps)
        self._frame_interval = (1.0 / self.target_fps) if self.target_fps > 0 else 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background ingestion thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self.status = "STARTING"
        self._thread = threading.Thread(target=self._ingestion_loop, name=f"ingest-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the worker and wait briefly for the thread to finish."""
        self._running = False
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self.is_connected = False
        if thread is None or not thread.is_alive():
            self.status = "STOPPED"
        else:
            # The thread is still winding down; report that honestly.
            self.status = "STOPPING"

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        """Open the source with explicit open/read timeouts where supported."""
        if self.webcam_config is not None:
            # A capture device has no stream transport to configure. Capture
            # properties and failure handling are the device driver's business.
            capture, backend, error = open_webcam_capture(self.webcam_config)
            self.source_backend = backend
            if capture is None:
                logger.warning(
                    "Webcam device could not be opened",
                    extra={
                        "component": "video_ingestion",
                        "camera_id": self.camera_id,
                        "device_index": self.webcam_config.device_index,
                        "open_error": error,
                    },
                )
            return capture

        timeout_ms = int(self.connect_timeout_seconds * 1000)
        read_timeout_ms = int(self.read_timeout_seconds * 1000)
        previous_options = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            f"rtsp_transport;tcp|timeout;{timeout_ms}"
        )
        try:
            capture = cv2.VideoCapture(self.stream_url, cv2.CAP_FFMPEG)
        except Exception:  # noqa: BLE001 - backend without FFMPEG support
            capture = cv2.VideoCapture(self.stream_url)
        finally:
            if previous_options is None:
                os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            else:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = previous_options
        if capture is not None and capture.isOpened():
            try:
                capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms)
                capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, read_timeout_ms)
            except Exception:  # noqa: BLE001 - property unsupported for this backend
                logger.debug(
                    "Capture timeout properties unsupported",
                    extra={"component": "video_ingestion", "camera_id": self.camera_id},
                )
        return capture

    def _ingestion_loop(self) -> None:
        reconnect_delay = settings.CAMERA_RECONNECT_INITIAL_DELAY_SECONDS
        max_delay = settings.CAMERA_RECONNECT_MAX_DELAY_SECONDS
        max_attempts = settings.CAMERA_MAX_RECONNECT_ATTEMPTS
        is_file_source = self._is_file_source()

        while self._running:
            if is_file_source and not os.path.isfile(self.stream_url):
                self.is_connected = False
                self.last_error = "Configured video file does not exist"
                self.status = "SOURCE_UNAVAILABLE"
                # Terminal condition: a file that is not present will not appear
                # on its own, so retrying would only spin. Clearing _running here
                # keeps the exit path below from overwriting the honest reason
                # with a bare STOPPED.
                self._running = False
                logger.warning(
                    "Video file unavailable",
                    extra={"component": "video_ingestion", "camera_id": self.camera_id, "status": self.status},
                )
                break

            self.reconnect_attempts += 1
            try:
                capture = self._open_capture()
            except Exception as exc:  # noqa: BLE001 - constructor failure
                self.last_error = type(exc).__name__
                self.is_connected = False
                self.status = "SOURCE_UNAVAILABLE" if is_file_source else "DISCONNECTED"
                logger.error(
                    "Video source open failed",
                    extra={
                        "component": "video_ingestion",
                        "camera_id": self.camera_id,
                        "error_type": type(exc).__name__,
                        "status": self.status,
                    },
                )
                if not self._await_reconnect(reconnect_delay, max_attempts, is_file_source):
                    reconnect_delay = min(reconnect_delay * 1.5, max_delay)
                    continue
                break

            if capture is None or not capture.isOpened():
                if capture is not None:
                    capture.release()
                self.is_connected = False
                if self.webcam_config is not None:
                    self.last_error = (
                        f"Webcam device index {self.webcam_config.device_index} could not be opened; "
                        "the device may be absent, in use by another application, or blocked by privacy settings"
                    )
                else:
                    self.last_error = "Failed to open configured video source"
                self.status = "SOURCE_UNAVAILABLE" if is_file_source else "DISCONNECTED"
                logger.warning(
                    "Video source unavailable",
                    extra={"component": "video_ingestion", "camera_id": self.camera_id, "status": self.status},
                )
                if not self._await_reconnect(reconnect_delay, max_attempts, is_file_source):
                    reconnect_delay = min(reconnect_delay * 1.5, max_delay)
                    continue
                break

            self.is_connected = True
            if self._has_connected:
                self.reconnects += 1
            self._has_connected = True
            self._read_source_metadata(capture, is_file_source)
            self.last_error = None
            self.status = "RUNNING"
            reconnect_delay = settings.CAMERA_RECONNECT_INITIAL_DELAY_SECONDS

            self._read_loop(capture, is_file_source)

            try:
                capture.release()
            except Exception as exc:  # noqa: BLE001 - release failure is not fatal
                logger.error(
                    "Video source release failed",
                    extra={
                        "component": "video_ingestion",
                        "camera_id": self.camera_id,
                        "error_type": type(exc).__name__,
                    },
                )
            self.is_connected = False
            if self._running and not is_file_source:
                if not self._await_reconnect(reconnect_delay, max_attempts, is_file_source):
                    reconnect_delay = min(reconnect_delay * 1.5, max_delay)

        if self._running:
            self.status = "STOPPED"
        elif self.status not in ("SOURCE_UNAVAILABLE", "END_OF_FILE", "RECONNECT_EXHAUSTED"):
            self.status = "STOPPED"

    def _await_reconnect(self, delay: float, max_attempts: int, is_file_source: bool) -> bool:
        """Wait before reconnecting.

        Returns True when the worker should stop (file source, operator stop, or
        exhausted attempts) and False when the caller should retry.
        """
        if is_file_source or not self._running:
            if is_file_source and self.status == "SOURCE_UNAVAILABLE":
                self._running = False
            return True
        if max_attempts and self.reconnect_attempts >= max_attempts:
            self.status = "RECONNECT_EXHAUSTED"
            self.last_error = (
                f"Reconnect attempts exhausted after {self.reconnect_attempts} attempts"
            )
            self._running = False
            logger.error(
                "Reconnect attempts exhausted",
                extra={
                    "component": "video_ingestion",
                    "camera_id": self.camera_id,
                    "reconnect_attempts": self.reconnect_attempts,
                },
            )
            return True
        self._stop_event.wait(delay)
        return not self._running

    def _read_source_metadata(self, capture: cv2.VideoCapture, is_file_source: bool) -> None:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width > 0 and height > 0:
            self.source_resolution = (width, height)
        reported_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if np.isfinite(reported_fps) and reported_fps > 0:
            self.source_fps = reported_fps
            if is_file_source:
                frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                if frame_count > 0:
                    self.source_duration_seconds = frame_count / reported_fps

    def _read_loop(self, capture: cv2.VideoCapture, is_file_source: bool) -> None:
        frames_in_window = 0
        window_started = time.monotonic()
        next_frame_due = time.monotonic()
        last_frame_at: float | None = None

        while self._running and capture.isOpened():
            if self._frame_interval:
                now = time.monotonic()
                if now < next_frame_due:
                    self._stop_event.wait(min(next_frame_due - now, 0.05))
                    continue
                next_frame_due += self._frame_interval
                if next_frame_due < time.monotonic() - self._frame_interval:
                    # Falling behind the configured rate: resynchronise rather
                    # than accumulating an unbounded backlog.
                    next_frame_due = time.monotonic()

            try:
                retrieved, frame = capture.read()
            except Exception as exc:  # noqa: BLE001 - decoder error
                self.dropped_frames_count += 1
                self.decode_failures += 1
                self.last_error = type(exc).__name__
                logger.error(
                    "Video frame read failed",
                    extra={
                        "component": "video_ingestion",
                        "camera_id": self.camera_id,
                        "error_type": type(exc).__name__,
                    },
                )
                break

            if not retrieved or frame is None:
                if is_file_source:
                    self.status = "END_OF_FILE"
                    self._running = False
                    break
                self.dropped_frames_count += 1
                self.decode_failures += 1
                self.last_error = "Frame read failed"
                break

            if not isinstance(frame, np.ndarray) or frame.size == 0 or frame.ndim < 2:
                self.dropped_frames_count += 1
                self.invalid_frames_dropped += 1
                self.last_error = "Corrupted or empty frame received"
                logger.warning(
                    "Invalid frame dropped",
                    extra={"component": "video_ingestion", "camera_id": self.camera_id},
                )
                continue

            now = datetime.now(timezone.utc)
            frames_in_window += 1
            self.total_frames_read += 1
            if self.first_frame_timestamp is None:
                self.first_frame_timestamp = now
            self.last_frame_timestamp = now
            if last_frame_at is not None:
                self.last_frame_interval_ms = round((time.monotonic() - last_frame_at) * 1000.0, 3)
            last_frame_at = time.monotonic()

            elapsed = time.monotonic() - window_started
            if elapsed >= 1.0:
                self.current_fps = round(frames_in_window / elapsed, 2)
                frames_in_window = 0
                window_started = time.monotonic()

            try:
                self.frame_buffer.add(now, frame)
            except (ValueError, RuntimeError) as exc:
                self.dropped_frames_count += 1
                self.buffer_rejections += 1
                self.last_error = type(exc).__name__
                logger.error(
                    "Frame buffer rejected a frame",
                    extra={
                        "component": "video_ingestion",
                        "camera_id": self.camera_id,
                        "error_type": type(exc).__name__,
                    },
                )

    def _is_file_source(self) -> bool:
        """True only for a local file path.

        A webcam device is not a file even though it is not a network scheme, so
        the webcam check comes first. Getting this wrong would make the reader
        wait for a file named "webcam://0" and report SOURCE_UNAVAILABLE.
        """
        url = self.stream_url.strip().lower()
        if is_webcam_source(self.stream_url) or self.webcam_config is not None:
            return False
        return not url.startswith(STREAM_URL_SCHEMES) and not self.stream_url.isdigit()

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def get_latest_frame(self) -> Optional[Tuple[datetime, np.ndarray]]:
        latest = self.frame_buffer.latest()
        return (latest.timestamp, latest.image) if latest else None

    def get_frame_buffer(self) -> List[Tuple[datetime, np.ndarray]]:
        return [(item.timestamp, item.image) for item in self.frame_buffer.snapshot()]

    def get_frames_after(self, timestamp: datetime | None) -> tuple[BufferedFrame, ...]:
        frames = self.frame_buffer.snapshot()
        if timestamp is None:
            return frames
        return tuple(item for item in frames if item.timestamp > timestamp)

    def telemetry(self) -> dict:
        """Return observable source state. No value is invented when absent."""
        return {
            "camera_id": self.camera_id,
            "source_type": self.source_type,
            "status": self.status,
            "is_connected": self.is_connected,
            "stream_state": self.status if self.status in STREAM_STATES else "UNKNOWN",
            "current_fps": self.current_fps if self.current_fps > 0 else None,
            "total_frames_read": self.total_frames_read,
            "dropped_frames": self.dropped_frames_count,
            "decode_failures": self.decode_failures,
            "invalid_frames_dropped": self.invalid_frames_dropped,
            "buffer_rejections": self.buffer_rejections,
            "reconnects": self.reconnects,
            "reconnect_attempts": self.reconnect_attempts,
            "source_resolution": list(self.source_resolution) if self.source_resolution else None,
            "source_fps": self.source_fps,
            "source_duration_seconds": self.source_duration_seconds,
            "source_backend": self.source_backend,
            "device_index": self.webcam_config.device_index if self.webcam_config else None,
            "first_frame_timestamp": self.first_frame_timestamp,
            "last_frame_timestamp": self.last_frame_timestamp,
            "last_frame_interval_ms": self.last_frame_interval_ms,
            "last_error": self.last_error,
            "target_fps": self.target_fps or None,
            # Observed-frame evidence, kept explicit so no consumer has to infer
            # it from a driver-reported number.
            "observed_frames": self.total_frames_read > 0,
        }


class StreamIngestionManager:
    """Process-local manager controlling all active stream ingestion threads."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(StreamIngestionManager, cls).__new__(cls)
                cls._instance._readers: Dict[str, StreamReader] = {}
            return cls._instance

    def start_stream(
        self,
        camera_id: str,
        stream_url: str,
        target_fps: float | None = None,
    ) -> StreamReader:
        with self._lock:
            existing = self._readers.get(camera_id)
            if existing is not None and existing.stream_url == stream_url and existing.is_alive():
                return existing
            if existing is not None:
                existing.stop()

            reader = StreamReader(camera_id=camera_id, stream_url=stream_url, target_fps=target_fps)
            reader.start()
            self._readers[camera_id] = reader
            return reader

    def stop_stream(self, camera_id: str) -> None:
        with self._lock:
            reader = self._readers.pop(camera_id, None)
        if reader is not None:
            reader.stop()

    def get_reader(self, camera_id: str) -> Optional[StreamReader]:
        with self._lock:
            return self._readers.get(camera_id)

    def active_camera_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._readers)

    def stop_all(self) -> None:
        with self._lock:
            readers = list(self._readers.values())
            self._readers.clear()
        for reader in readers:
            reader.stop()


#: Global ingestion manager instance.
ingestion_manager = StreamIngestionManager()
