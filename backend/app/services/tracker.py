"""Greedy IoU multi-object tracker with visual-only track identifiers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from ai_models.detection import Detection


TrackState = str


@dataclass(frozen=True)
class TrackPoint:
    timestamp: datetime
    bbox: tuple[float, float, float, float]


@dataclass
class VisualTrack:
    track_id: str
    camera_id: str
    class_name: str
    first_seen: datetime
    last_seen: datetime
    bbox: tuple[float, float, float, float]
    confidence: float
    trajectory: list[TrackPoint] = field(default_factory=list)
    lifecycle_state: TrackState = "DETECTED"


class IoUTracker:
    def __init__(self, iou_threshold: float = 0.3, max_lost_seconds: float = 2.0, max_history: int = 1000) -> None:
        if not 0.0 <= iou_threshold <= 1.0 or max_lost_seconds < 0 or max_history < 1:
            raise ValueError("Invalid tracker configuration")
        self.iou_threshold = iou_threshold
        self.max_lost_seconds = max_lost_seconds
        self.max_history = max_history
        self._tracks: dict[str, VisualTrack] = {}
        self._lock = RLock()

    def update(self, camera_id: str, detections: list[Detection], timestamp: datetime) -> tuple[VisualTrack, ...]:
        now = self._utc(timestamp)
        with self._lock:
            candidates = [
                track for track in self._tracks.values()
                if track.camera_id == camera_id and track.lifecycle_state != "ENDED"
            ]
            for track in candidates:
                if (now - track.last_seen).total_seconds() > self.max_lost_seconds:
                    track.lifecycle_state = "ENDED"

            available_tracks = [track for track in candidates if track.lifecycle_state != "ENDED"]
            pairs = sorted(
                (
                    (self._iou(track.bbox, detection.bbox), track, index)
                    for track in available_tracks
                    for index, detection in enumerate(detections)
                    if detection.camera_id == camera_id and track.class_name == detection.class_name
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            matched_tracks: set[str] = set()
            matched_detections: set[int] = set()
            for overlap, track, index in pairs:
                if overlap < self.iou_threshold or track.track_id in matched_tracks or index in matched_detections:
                    continue
                self._observe(track, detections[index], now, "TRACKED")
                matched_tracks.add(track.track_id)
                matched_detections.add(index)

            for track in available_tracks:
                if track.track_id not in matched_tracks:
                    track.lifecycle_state = "TEMPORARILY_LOST"

            for index, detection in enumerate(detections):
                if index in matched_detections or detection.camera_id != camera_id:
                    continue
                track_id = f"{camera_id}_track_{uuid4().hex[:12]}"
                track = VisualTrack(
                    track_id=track_id,
                    camera_id=camera_id,
                    class_name=detection.class_name,
                    first_seen=now,
                    last_seen=now,
                    bbox=detection.bbox,
                    confidence=detection.confidence,
                )
                self._observe(track, detection, now, "DETECTED")
                self._tracks[track_id] = track

            return tuple(self._copy_track(track) for track in self._tracks.values() if track.camera_id == camera_id)

    def get_tracks(self, camera_id: str | None = None) -> tuple[VisualTrack, ...]:
        with self._lock:
            return tuple(
                self._copy_track(track) for track in self._tracks.values()
                if camera_id is None or track.camera_id == camera_id
            )

    def _observe(self, track: VisualTrack, detection: Detection, now: datetime, state: TrackState) -> None:
        track.last_seen = now
        track.bbox = detection.bbox
        track.confidence = detection.confidence
        track.lifecycle_state = state
        track.trajectory.append(TrackPoint(now, detection.bbox))
        if len(track.trajectory) > self.max_history:
            del track.trajectory[:-self.max_history]

    @staticmethod
    def _copy_track(track: VisualTrack) -> VisualTrack:
        return VisualTrack(
            track_id=track.track_id,
            camera_id=track.camera_id,
            class_name=track.class_name,
            first_seen=track.first_seen,
            last_seen=track.last_seen,
            bbox=track.bbox,
            confidence=track.confidence,
            trajectory=list(track.trajectory),
            lifecycle_state=track.lifecycle_state,
        )

    @staticmethod
    def _utc(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    @staticmethod
    def _iou(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
        x1 = max(first[0], second[0])
        y1 = max(first[1], second[1])
        x2 = min(first[2], second[2])
        y2 = min(first[3], second[3])
        intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        first_area = (first[2] - first[0]) * (first[3] - first[1])
        second_area = (second[2] - second[0]) * (second[3] - second[1])
        union = first_area + second_area - intersection
        return intersection / union if union else 0.0