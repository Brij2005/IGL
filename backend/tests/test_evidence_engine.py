from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base
from app.models import Camera, Event, EventEvidence
from app.services.evidence_engine import EvidenceEngine


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def persisted_test_event(db):
    camera = Camera(name="test camera", code="EVIDENCE-CAM", stream_url="test-only-source")
    db.add(camera)
    db.flush()
    event = Event(camera_id=camera.id, event_type="TEST_ONLY", confidence=0.5)
    db.add(event)
    db.commit()
    return event


def test_evidence_requires_real_existing_event_and_matching_camera(db_session, tmp_path):
    engine = EvidenceEngine(tmp_path)
    with pytest.raises(ValueError, match="existing event"):
        engine.capture_snapshot(
            db_session,
            event_id="missing-event",
            camera_id="camera",
            frame_timestamp=datetime.now(timezone.utc),
            frame=np.ones((2, 2, 3), dtype=np.uint8),
        )
    event = persisted_test_event(db_session)
    with pytest.raises(ValueError, match="must match"):
        engine.capture_snapshot(
            db_session,
            event_id=event.id,
            camera_id="different-camera",
            frame_timestamp=datetime.now(timezone.utc),
            frame=np.ones((2, 2, 3), dtype=np.uint8),
        )
    assert db_session.query(EventEvidence).count() == 0
    assert list(Path(tmp_path).glob("*")) == []


def test_evidence_returns_unavailable_without_a_frame(db_session, tmp_path):
    event = persisted_test_event(db_session)
    result = EvidenceEngine(tmp_path).capture_snapshot(
        db_session,
        event_id=event.id,
        camera_id=event.camera_id,
        frame_timestamp=datetime.now(timezone.utc),
        frame=None,
    )
    assert result.status == "EVIDENCE_NOT_AVAILABLE"
    assert result.evidence is None
    assert db_session.query(EventEvidence).count() == 0


def test_evidence_hashes_and_persists_captured_frame(db_session, tmp_path):
    event = persisted_test_event(db_session)
    engine = EvidenceEngine(tmp_path)
    frame = np.full((8, 8, 3), 128, dtype=np.uint8)
    result = engine.capture_snapshot(
        db_session,
        event_id=event.id,
        camera_id=event.camera_id,
        frame_timestamp=datetime.now(timezone.utc),
        frame=frame,
    )
    assert result.status == "CAPTURED"
    assert len(result.evidence.file_hash) == 64
    assert engine.resolve_path(result.evidence).is_file()
    assert db_session.query(EventEvidence).filter_by(event_id=event.id).count() == 1


def test_evidence_integrity_check_rejects_modified_file(db_session, tmp_path):
    event = persisted_test_event(db_session)
    engine = EvidenceEngine(tmp_path)
    result = engine.capture_snapshot(
        db_session,
        event_id=event.id,
        camera_id=event.camera_id,
        frame_timestamp=datetime.now(timezone.utc),
        frame=np.full((8, 8, 3), 64, dtype=np.uint8),
    )
    path = engine.resolve_path(result.evidence)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(FileNotFoundError, match="integrity"):
        engine.resolve_path(result.evidence)
