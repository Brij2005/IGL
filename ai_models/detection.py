"""Validated model detection result; it carries no employee identity."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from uuid import uuid4


@dataclass(frozen=True)
class Detection:
    camera_id: str
    timestamp: datetime
    class_name: str
    confidence: float
    bbox: tuple[float, float, float, float]
    frame_timestamp: datetime
    model_name: str
    model_version: str
    detection_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.camera_id.strip() or not self.class_name.strip():
            raise ValueError("camera_id and class_name are required")
        if not self.model_name.strip() or not self.model_version.strip():
            raise ValueError("model name and version are required")
        if not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be finite and between 0 and 1")
        if len(self.bbox) != 4 or not all(isfinite(value) for value in self.bbox):
            raise ValueError("bbox must contain four finite coordinates")
        x1, y1, x2, y2 = self.bbox
        if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError("bbox coordinates must define a positive-area image region")
        for name in ("timestamp", "frame_timestamp"):
            value = getattr(self, name)
            if value.tzinfo is None:
                object.__setattr__(self, name, value.replace(tzinfo=timezone.utc))
            else:
                object.__setattr__(self, name, value.astimezone(timezone.utc))