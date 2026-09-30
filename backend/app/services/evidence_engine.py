"""Evidence capture from actual buffered frames and persisted events."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.models import Event, EventEvidence
except ImportError:
    from backend.app.config import settings
    from backend.app.models import Event, EventEvidence


logger = logging.getLogger("igl.evidence")


@dataclass(frozen=True)
class EvidenceCaptureResult:
    status: str
    evidence: EventEvidence | None
    reason: str | None = None


class EvidenceEngine:
    def __init__(self, evidence_dir: str | Path | None = None):
        self.evidence_dir = Path(evidence_dir or settings.EVIDENCE_DIR).expanduser().resolve()

    def capture_snapshot(
        self,
        db: Session,
        *,
        event_id: str,
        camera_id: str,
        frame_timestamp: datetime,
        frame: np.ndarray | None,
    ) -> EvidenceCaptureResult:
        event = db.query(Event).filter(Event.id == event_id).first()
        if event is None:
            raise ValueError("Evidence cannot be created without an existing event")
        if event.camera_id != camera_id:
            raise ValueError("Evidence camera must match the event camera")
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            return EvidenceCaptureResult("EVIDENCE_NOT_AVAILABLE", None, "No actual frame is available")

        encoded_ok, encoded = cv2.imencode(".jpg", frame)
        if not encoded_ok:
            return EvidenceCaptureResult("EVIDENCE_NOT_AVAILABLE", None, "Frame encoding failed")
        payload = encoded.tobytes()
        digest = hashlib.sha256(payload).hexdigest()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.evidence_dir / f"{event_id}_{uuid4().hex}.jpg"
        temporary_path = file_path.with_suffix(".jpg.tmp")
        try:
            temporary_path.write_bytes(payload)
            temporary_path.replace(file_path)
            evidence = EventEvidence(
                event_id=event_id,
                evidence_type="SNAPSHOT",
                file_path=str(file_path),
                file_hash=digest,
                metadata_json={
                    "camera_id": camera_id,
                    "frame_timestamp": self._utc(frame_timestamp).isoformat(),
                    "capture_status": "CAPTURED",
                },
            )
            db.add(evidence)
            db.commit()
            db.refresh(evidence)
            return EvidenceCaptureResult("CAPTURED", evidence)
        except Exception:
            db.rollback()
            temporary_path.unlink(missing_ok=True)
            file_path.unlink(missing_ok=True)
            logger.exception("Evidence persistence failed", extra={"component": "evidence", "event_id": event_id, "camera_id": camera_id})
            raise

    def resolve_path(self, evidence: EventEvidence) -> Path:
        path = Path(evidence.file_path).resolve()
        if self.evidence_dir not in path.parents or not path.is_file():
            raise FileNotFoundError("Evidence file is unavailable or outside configured storage")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not evidence.file_hash or digest != evidence.file_hash:
            raise FileNotFoundError("Evidence integrity check failed")
        return path

    @staticmethod
    def _utc(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)


evidence_engine = EvidenceEngine()