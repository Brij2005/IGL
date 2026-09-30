"""
Threaded Video Ingestion Pipeline supporting RTSP, IP, USB, and File video streams.
Includes thread-safe circular frame buffering, FPS tracking, and reconnection logic.
"""
import time
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, List
import cv2
import numpy as np


class StreamReader:
    """
    Non-blocking worker thread reading video frames from an RTSP / IP stream or video file.
    Stores recent frames in a thread-safe circular ring buffer.
    """

    def __init__(self, camera_id: str, stream_url: str, target_fps: float = 25.0):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.target_fps = target_fps

        self.buffer_maxlen = 100
        self._frame_buffer = deque(maxlen=self.buffer_maxlen)
        self._buffer_lock = threading.Lock()

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Telemetry stats
        self.current_fps: float = 0.0
        self.total_frames_read: int = 0
        self.dropped_frames_count: int = 0
        self.last_frame_timestamp: Optional[datetime] = None
        self.is_connected: bool = False
        self.last_error: Optional[str] = None

    def start(self):
        """Start the background ingestion thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._ingestion_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the background ingestion thread cleanly."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _ingestion_loop(self):
        """Main frame acquisition loop with automatic reconnection logic."""
        reconnect_delay = 1.0
        max_reconnect_delay = 15.0

        while self._running:
            cap = cv2.VideoCapture(self.stream_url)
            if not cap.isOpened():
                self.is_connected = False
                self.last_error = f"Failed to connect to stream: {self.stream_url}"
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
                continue

            self.is_connected = True
            self.last_error = None
            reconnect_delay = 1.0  # Reset reconnect delay on success

            frame_count = 0
            start_time = time.time()

            while self._running and cap.isOpened():
                ret, frame = cap.read()
                if not ret or frame is None:
                    self.dropped_frames_count += 1
                    time.sleep(0.01)
                    # If file stream reached end, loop or break
                    if isinstance(self.stream_url, str) and not self.stream_url.startswith("rtsp://") and not self.stream_url.startswith("http"):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # Loop video file for continuous testing
                        continue
                    break

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

                # Push to thread-safe circular buffer
                with self._buffer_lock:
                    self._frame_buffer.append((now, frame))

            cap.release()
            self.is_connected = False

    def get_latest_frame(self) -> Optional[Tuple[datetime, np.ndarray]]:
        """Retrieve the most recent frame from the buffer."""
        with self._buffer_lock:
            if not self._frame_buffer:
                return None
            return self._frame_buffer[-1]

    def get_frame_buffer(self) -> List[Tuple[datetime, np.ndarray]]:
        """Retrieve a snapshot of the current frame buffer."""
        with self._buffer_lock:
            return list(self._frame_buffer)


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
