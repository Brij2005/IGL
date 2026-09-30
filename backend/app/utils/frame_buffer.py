"""Bounded, thread-safe frame retention and post-event recording."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Literal
from uuid import uuid4

import numpy as np


@dataclass(frozen=True)
class BufferedFrame:
    timestamp: datetime
    image: np.ndarray = field(repr=False, compare=False)

    @property
    def size_bytes(self) -> int:
        return int(self.image.nbytes)


@dataclass(frozen=True)
class RecordingSnapshot:
    recording_id: str
    event_timestamp: datetime
    status: Literal["RECORDING", "COMPLETE", "TRUNCATED"]
    frames: tuple[BufferedFrame, ...]


@dataclass
class _Recording:
    recording_id: str
    event_timestamp: datetime
    deadline: datetime
    frames: list[BufferedFrame]
    status: Literal["RECORDING", "COMPLETE", "TRUNCATED"]


def _utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


class FrameBuffer:
    """Chronologically ordered rolling frames with frame and byte ceilings."""

    def __init__(
        self,
        retention_seconds: float = 30.0,
        max_frames: int = 100,
        max_bytes: int = 67_108_864,
        max_recordings: int = 100,
    ) -> None:
        if retention_seconds <= 0 or max_frames < 1 or max_bytes < 1 or max_recordings < 1:
            raise ValueError("Buffer retention and limits must be positive")
        self.retention_seconds = retention_seconds
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self.max_recordings = max_recordings
        self._frames: list[BufferedFrame] = []
        self._recordings: dict[str, _Recording] = {}
        self._lock = RLock()

    def add(self, timestamp: datetime, frame: np.ndarray) -> BufferedFrame:
        """Insert a defensive, read-only frame copy or reject an invalid/oversize frame."""
        if not isinstance(frame, np.ndarray) or frame.size == 0 or frame.ndim < 2:
            raise ValueError("Frame must be a non-empty NumPy image")
        image = np.array(frame, copy=True)
        size_bytes = int(image.nbytes)
        if size_bytes > self.max_bytes:
            raise ValueError("Frame exceeds the configured buffer memory limit")
        image.setflags(write=False)
        record = BufferedFrame(_utc(timestamp), image)

        with self._lock:
            index = bisect_right([item.timestamp for item in self._frames], record.timestamp)
            self._frames.insert(index, record)
            for recording in self._recordings.values():
                if recording.status == "RECORDING" and recording.event_timestamp < record.timestamp:
                    if record.timestamp <= recording.deadline:
                        recording.frames.append(record)
                        recording.frames.sort(key=lambda item: item.timestamp)
                    if record.timestamp >= recording.deadline:
                        recording.status = "COMPLETE"
            self._prune_locked()
            self._cleanup_recordings_locked(self._frames[-1].timestamp)
            self._enforce_limits_locked()
        return record

    def latest(self) -> BufferedFrame | None:
        with self._lock:
            return self._frames[-1] if self._frames else None

    def snapshot(self) -> tuple[BufferedFrame, ...]:
        with self._lock:
            return tuple(self._frames)

    def get_pre_event(self, event_timestamp: datetime, seconds: float) -> tuple[BufferedFrame, ...]:
        if seconds < 0:
            raise ValueError("Pre-event duration cannot be negative")
        event_time = _utc(event_timestamp)
        start_time = event_time - timedelta(seconds=seconds)
        with self._lock:
            return tuple(item for item in self._frames if start_time <= item.timestamp <= event_time)

    def start_event_recording(
        self,
        event_timestamp: datetime,
        pre_event_seconds: float,
        post_event_seconds: float,
    ) -> RecordingSnapshot:
        if pre_event_seconds < 0 or post_event_seconds < 0:
            raise ValueError("Event recording durations cannot be negative")
        event_time = _utc(event_timestamp)
        deadline = event_time + timedelta(seconds=post_event_seconds)
        with self._lock:
            if len(self._recordings) >= self.max_recordings:
                completed = [key for key, item in self._recordings.items() if item.status != "RECORDING"]
                if completed:
                    del self._recordings[completed[0]]
                else:
                    raise RuntimeError("Maximum active event recordings reached")
            initial = [
                item for item in self._frames
                if event_time - timedelta(seconds=pre_event_seconds) <= item.timestamp <= deadline
            ]
            recording_id = str(uuid4())
            status: Literal["RECORDING", "COMPLETE", "TRUNCATED"] = (
                "COMPLETE" if post_event_seconds == 0 or self._frames and self._frames[-1].timestamp >= deadline
                else "RECORDING"
            )
            recording = _Recording(recording_id, event_time, deadline, initial, status)
            self._recordings[recording_id] = recording
            self._enforce_limits_locked()
            return self._snapshot_recording(recording)

    def get_recording(self, recording_id: str) -> RecordingSnapshot | None:
        with self._lock:
            recording = self._recordings.get(recording_id)
            return self._snapshot_recording(recording) if recording else None

    def cleanup(self, now: datetime | None = None) -> None:
        """Remove expired rolling frames and completed recordings past retention."""
        cutoff = _utc(now or datetime.now(timezone.utc)) - timedelta(seconds=self.retention_seconds)
        with self._lock:
            self._frames = [item for item in self._frames if item.timestamp >= cutoff]
            current = _utc(now or datetime.now(timezone.utc))
            self._cleanup_recordings_locked(current)
            self._enforce_limits_locked()

    @property
    def memory_bytes(self) -> int:
        with self._lock:
            return self._memory_bytes_locked()

    def _prune_locked(self) -> None:
        if not self._frames:
            return
        cutoff = self._frames[-1].timestamp - timedelta(seconds=self.retention_seconds)
        self._frames = [item for item in self._frames if item.timestamp >= cutoff]

    def _memory_bytes_locked(self) -> int:
        unique = {id(item): item for item in self._frames}
        for recording in self._recordings.values():
            unique.update({id(item): item for item in recording.frames})
        return sum(item.size_bytes for item in unique.values())

    def _cleanup_recordings_locked(self, now: datetime) -> None:
        for recording in self._recordings.values():
            if recording.status == "RECORDING" and recording.deadline <= now:
                recording.status = "TRUNCATED"
                recording.frames.clear()
        expired = [
            key for key, item in self._recordings.items()
            if item.status != "RECORDING" and item.deadline + timedelta(seconds=self.retention_seconds) < now
        ]
        for key in expired:
            del self._recordings[key]

    def _enforce_limits_locked(self) -> None:
        while len(self._frames) > self.max_frames or self._frame_count_locked() > self.max_frames:
            candidates = [item for item in self._recordings.values() if item.status != "TRUNCATED"]
            if candidates and (len(self._frames) <= self.max_frames or self._frame_count_locked() > self.max_frames):
                oldest = min(candidates, key=lambda item: item.event_timestamp)
                oldest.frames.clear()
                oldest.status = "TRUNCATED"
            elif len(self._frames) > 1:
                self._frames.pop(0)
            else:
                break
        while self._memory_bytes_locked() > self.max_bytes:
            candidates = [item for item in self._recordings.values() if item.status != "TRUNCATED"]
            if candidates:
                oldest = min(candidates, key=lambda item: item.event_timestamp)
                oldest.frames.clear()
                oldest.status = "TRUNCATED"
                continue
            if len(self._frames) > 1:
                self._frames.pop(0)
            else:
                raise RuntimeError("Frame buffer could not enforce its configured byte limit")

    def _frame_count_locked(self) -> int:
        unique = {id(item) for item in self._frames}
        for recording in self._recordings.values():
            unique.update(id(item) for item in recording.frames)
        return len(unique)

    @staticmethod
    def _snapshot_recording(recording: _Recording) -> RecordingSnapshot:
        return RecordingSnapshot(
            recording_id=recording.recording_id,
            event_timestamp=recording.event_timestamp,
            status=recording.status,
            frames=tuple(recording.frames),
        )