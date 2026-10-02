"""Worker monitoring is built only from stored tracking/inference records."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base, get_db
from app.main import app, seed_reference_roles
from app.models import Area, Camera, Detection, Plant, Role, Track, User, Zone


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    seed_reference_roles(session)
    admin_role = session.query(Role).filter_by(name="ADMIN").one()
    session.add(User(username="tracks_test_admin", email="tracks@example.test", full_name="Test Admin", role_id=admin_role.id))
    plant = Plant(name="Test plant", code="TRACK_TEST_PLANT")
    session.add(plant)
    session.flush()
    area = Area(plant_id=plant.id, name="Test area", code="TRACK_TEST_AREA")
    session.add(area)
    session.flush()
    zone = Zone(area_id=area.id, name="Test zone", code="TRACK_TEST_ZONE", zone_type="WORK_AREA")
    session.add(zone)
    session.flush()
    camera = Camera(name="Test camera", code="TRACK_TEST_CAMERA", stream_url="webcam://0", camera_type="WEBCAM", zone_id=zone.id)
    session.add(camera)
    session.flush()
    now = datetime.now(timezone.utc)
    track = Track(camera_id=camera.id, track_uuid="track-real-record", object_class="PERSON", first_seen_at=now - timedelta(seconds=20), last_seen_at=now - timedelta(seconds=3))
    session.add(track)
    session.flush()
    detection = Detection(camera_id=camera.id, track_id=track.id, timestamp=now - timedelta(seconds=3), object_class="PERSON", confidence=0.91, bbox_json=[0.1, 0.2, 0.3, 0.4], observation_state="POSSIBLE")
    session.add(detection)
    session.commit()
    return engine, session, track


def test_worker_tracks_api_returns_persisted_track_and_never_person_identity():
    engine, session, track = _session()
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/workers/tracks?recent_only=true&recent_within_seconds=30")
        assert response.status_code == 200
        records = response.json()
        assert len(records) == 1
        record = records[0]
        assert record["track_id"] == "track-real-record"
        assert record["camera_name"] == "Test camera"
        assert record["zone_name"] == "Test zone"
        assert record["freshness"] == "RECENTLY_OBSERVED"
        assert record["latest_detection"]["object_class"] == "PERSON"
        assert record["latest_detection"]["confidence"] == 0.91
        assert record["helmet_status"] == "UNKNOWN"
        assert record["phone_status"] == "UNKNOWN"
        assert "employee_id" not in record and "employee_name" not in record
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_worker_tracks_recent_filter_does_not_present_stale_records_as_live():
    engine, session, track = _session()
    track.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/workers/tracks?recent_only=true&recent_within_seconds=5").json() == []
            historical = client.get("/api/v1/workers/tracks?recent_only=false").json()
        assert len(historical) == 1
        assert historical[0]["freshness"] == "STALE"
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
