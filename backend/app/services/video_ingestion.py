"""
Threaded Video Ingestion Pipeline supporting RTSP, IP, USB, and File video streams.
Includes thread-safe circular frame buffering, FPS tracking, and reconnection logic.
"""
import time
import threading
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, List
import logging
import cv2
import numpy as np

try:
    from app.config import settings
    from app.utils.frame_buffer import BufferedFrame, FrameBuffer
except ImportError:
    from backend.app.config import settings
    from backend.app.utils.frame_buffer import BufferedFrame, FrameBuffer


logger = logging.getLogger("igl.video.ingestion")


class StreamReader:
    """
    Non-blocking worker thread reading video frames from an RTSP / IP stream or video file.
    Stores recent frames in a thread-safe circular ring buffer.
    """

    def __init__(
        self,
        camera_id: str,
        stream_url: str,
        target_fps: float = 25.0,
        frame_buffer: FrameBuffer | None = None,
    ):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.target_fps = target_fps

        self.frame_buffer = frame_buffer or FrameBuffer(
            retention_seconds=settings.FRAME_BUFFER_RETENTION_SECONDS,
            max_frames=settings.FRAME_BUFFER_MAX_FRAMES,
            max_bytes=settings.FRAME_BUFFER_MAX_BYTES,
        )

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Telemetry stats
        self.current_fps: float = 0.0
        self.total_frames_read: int = 0
        self.dropped_frames_count: int = 0
        self.last_frame_timestamp: Optional[datetime] = None
        self.is_connected: bool = False
        self.last_error: Optional[str] = None
        self.status = "STOPPED"
        self._stop_event = threading.Event()

    def start(self):
        """Start the background ingestion thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self.status = "STARTING"
        self._thread = threading.Thread(target=self._ingestion_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the background ingestion thread cleanly."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.status = "STOPPED"

    def _ingestion_loop(self):
        """Main frame acquisition loop with automatic reconnection logic."""
        reconnect_delay = 1.0
        max_reconnect_delay = 15.0
        is_file_source = (
            not self.stream_url.lower().startswith(("rtsp://", "rtsps://", "http://", "https://"))
            and not self.stream_url.isdigit()
        )

        while self._running:
            if is_file_source and not os.path.isfile(self.stream_url):
                self.is_connected = False
                self.last_error = "Configured video file does not exist"
                self.status = "SOURCE_UNAVAILABLE"
                logger.warning("Video file unavailable", extra={"component": "video_ingestion", "camera_id": self.camera_id})
                self._running = False
                break
            cap = cv2.VideoCapture(self.stream_url)
            if not cap.isOpened():
                self.is_connected = False
                self.last_error = "Failed to open configured video source"
                self.status = "SOURCE_UNAVAILABLE" if is_file_source else "DISCONNECTED"
                logger.warning("Video source unavailable", extra={"component": "video_ingestion", "camera_id": self.camera_id})
                if is_file_source:
                    self._running = False
                    break
                self._stop_event.wait(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
                continue

            self.is_connected = True
            self.last_error = None
            self.status = "RUNNING"
            reconnect_delay = 1.0  # Reset reconnect delay on success

            frame_count = 0
            start_time = time.time()

            while self._running and cap.isOpened():
                ret, frame = cap.read()
                if not ret or frame is None:
                    self.dropped_frames_count += 1
                    if is_file_source:
                        self.status = "END_OF_FILE"
                        self._running = False
                        break
                    self.last_error = "Frame read failed"
                    break

                if not isinstance(frame, np.ndarray) or frame.size == 0 or frame.ndim < 2:
                    self.dropped_frames_count += 1
                    self.last_error = "Corrupted or empty frame received"
                    logger.warning("Invalid frame dropped", extra={"component": "video_ingestion", "camera_id": self.camera_id})
                    continue

                now = datetime.now(timezone.utc)
                frame_count += 1
                self.total_frames_read += 1
                self.last_frame_timestamp = now

                # Calculate live FPS
                elapsed = time.time() - start_time
                if elapsed >= 1.0:
                    self.current_fps = round(frame_count / elapsed, 2)
                    frame_count = 0
                    start_time = time.time()

                try:
                    self.frame_buffer.add(now, frame)
                except (ValueError, RuntimeError) as exc:
                    self.dropped_frames_count += 1
                    self.last_error = type(exc).__name__
                    logger.error("Frame buffer rejected a frame", extra={"component": "video_ingestion", "camera_id": self.camera_id, "error_type": type(exc).__name__})

            cap.release()
            self.is_connected = False
            if self._running and not is_file_source:
                self._stop_event.wait(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
        if self.status not in ("SOURCE_UNAVAILABLE", "END_OF_FILE"):
            self.status = "STOPPED"

    def get_latest_frame(self) -> Optional[Tuple[datetime, np.ndarray]]:
        """Retrieve the most recent frame from the buffer."""
        latest = self.frame_buffer.latest()
        return (latest.timestamp, latest.image) if latest else None

    def get_frame_buffer(self) -> List[Tuple[datetime, np.ndarray]]:
        """Retrieve a snapshot of the current frame buffer."""
        return [(item.timestamp, item.image) for item in self.frame_buffer.snapshot()]

    def get_frames_after(self, timestamp: datetime | None) -> tuple[BufferedFrame, ...]:
        frames = self.frame_buffer.snapshot()
        if timestamp is None:
            return frames
        return tuple(item for item in frames if item.timestamp > timestamp)


class StreamIngestionManager:
    """Singleton Manager controlling all active camera stream ingestion threads."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(StreamIngestionManager, cls).__new__(cls)
                cls._instance._readers: Dict[str, StreamReader] = {}
            return cls._instance

    def start_stream(self, camera_id: str, stream_url: str, target_fps: float = 25.0) -> StreamReader:
        """Start ingestion worker for a camera."""
        with self._lock:
            if camera_id in self._readers:
                reader = self._readers[camera_id]
                if reader.stream_url == stream_url and reader._running:
                    return reader
                reader.stop()

            reader = StreamReader(camera_id=camera_id, stream_url=stream_url, target_fps=target_fps)
            reader.start()
            self._readers[camera_id] = reader
            return reader

    def stop_stream(self, camera_id: str):
        """Stop ingestion worker for a camera."""
        with self._lock:
            if camera_id in self._readers:
                self._readers[camera_id].stop()
                del self._readers[camera_id]

    def get_reader(self, camera_id: str) -> Optional[StreamReader]:
        """Get running StreamReader worker for a camera."""
        with self._lock:
            return self._readers.get(camera_id)

    def stop_all(self):
        """Stop all stream workers on shutdown."""
        with self._lock:
            for reader in self._readers.values():
                reader.stop()
            self._readers.clear()


# Global ingestion manager instance
ingestion_manager = StreamIngestionManager()
