from pathlib import Path
import sys

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.database import Base
from app.models import Camera
from app.services.camera_manager import CameraManager
from app.schemas_camera import CameraCreate


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield engine, session
    finally:
        session.close()
        engine.dispose()


def camera_create(url):
    return CameraCreate(name="Test camera", code="ENCRYPT-CAM", stream_url=url)


def test_credential_bearing_url_is_rejected_without_encryption_key(monkeypatch, db_session):
    _, session = db_session
    monkeypatch.setattr(settings, "CAMERA_URL_ENCRYPTION_KEY", None)
    with pytest.raises(ValueError, match="CAMERA_URL_ENCRYPTION_KEY is required"):
        CameraManager.create_camera(session, camera_create("rtsp://user:password@camera.invalid/live"))
    assert session.query(Camera).count() == 0


def test_url_is_encrypted_at_rest_and_decrypted_only_for_ingestion(monkeypatch, db_session):
    engine, session = db_session
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "CAMERA_URL_ENCRYPTION_KEY", SecretStr(key))
    secret_url = "rtsp://user:password@camera.invalid/live?token=secret-token"
    camera = CameraManager.create_camera(session, camera_create(secret_url))
    with engine.connect() as connection:
        stored = connection.execute(text("select stream_url from cameras where id=:id"), {"id": camera.id}).scalar_one()
    assert stored.startswith("enc:v1:")
    assert "password" not in stored and "secret-token" not in stored
    assert camera.stream_url == secret_url


def test_unencrypted_url_without_credentials_is_allowed_without_key(monkeypatch, db_session):
    _, session = db_session
    monkeypatch.setattr(settings, "CAMERA_URL_ENCRYPTION_KEY", None)
    camera = CameraManager.create_camera(session, camera_create("rtsp://camera.invalid/live"))
    assert camera.stream_url == "rtsp://camera.invalid/live"
