"""Security regression tests.

Covers credential redaction, secret handling, login throttling, response
schemas, pagination bounds, correlation IDs, and camera-configuration audit
behaviour. Every credential used here is a test fixture created inside the
test; no real secret, camera, or production database is involved.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import (
    LoginRateLimiter,
    get_current_active_user,
    hash_password,
    verify_password,
)
from app.config import DEVELOPMENT_SECRET_KEY, Settings
from app.database import Base
from app.main import app, seed_default_roles
from app.models import AuditLog, Camera, CameraHealth, Role, User
from app.schemas import LoginRequest, UserCreate
from app.schemas_camera import CameraOut, sanitize_stream_url
from app.schemas_system import AnalyticsSummaryOut, NotificationOut
from app.utils.redaction import display_source_identifier, safe_source_identifier

TEST_ADMIN_PASSWORD = "test-only-admin-password"


@pytest.fixture
def session():
    """An isolated in-memory database shared across request threads."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///file:security_test_db?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False, "uri": True},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def secured_client(session):
    """A TestClient whose requests are served from an isolated test database."""
    from app.database import get_db

    seed_default_roles(session)
    admin_role = session.query(Role).filter(Role.name == "ADMIN").one()
    session.add(User(
        username="security_admin",
        email="security_admin@example.test",
        full_name="Security Test Administrator",
        hashed_password=hash_password(TEST_ADMIN_PASSWORD),
        role_id=admin_role.id,
        is_active=True,
    ))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, session
    app.dependency_overrides.clear()


def admin_headers(client) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "security_admin", "password": TEST_ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source",
    [
        "rtsp://operator:secret@camera.example/live?token=abc",
        "rtsp://operator:secret@camera.example:554/live?token=abc#frag",
        "https://secret@camera.example/live?password=hunter2",
        "rtsp://camera.example/live?token=abc",
    ],
)
def test_display_identifier_never_returns_credentials_or_query(source):
    displayed = display_source_identifier(source)
    assert "secret" not in displayed
    assert "operator" not in displayed
    assert "token" not in displayed
    assert "hunter2" not in displayed
    assert "?" not in displayed
    assert "#" not in displayed
    assert displayed.startswith(("rtsp://", "https://"))


def test_report_identifier_marks_credentials_without_revealing_them():
    identifier = safe_source_identifier("rtsp://operator:secret@camera.example/live?token=abc")
    assert identifier == "rtsp://***:***@camera.example/live"
    assert "secret" not in identifier
    assert "token=abc" not in identifier


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("C:\\authorized\\clip.mp4", "clip.mp4"),
        ("/srv/authorized/clip.mp4", "clip.mp4"),
        ("rtsp://camera.example:notaport/live", "rtsp://camera.example/live"),
        ("not a url at all", "not a url at all"),
    ],
)
def test_malformed_and_local_sources_are_reduced_safely(source, expected):
    assert display_source_identifier(source) == expected


def test_camera_response_schema_strips_credentials():
    now = datetime.now(timezone.utc)
    camera = CameraOut.model_validate({
        "id": "camera-1",
        "name": "Test camera",
        "code": "CAM-1",
        "stream_url": "rtsp://operator:secret@camera.example/live?token=abc",
        "camera_type": "RTSP",
        "fps": 25,
        "resolution": "1920x1080",
        "zone_id": None,
        "location_description": None,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    })
    serialized = camera.model_dump_json()
    assert "secret" not in serialized
    assert "token" not in serialized
    assert camera.stream_url == "rtsp://camera.example/live"
    assert sanitize_stream_url("rtsp://operator:secret@camera.example:554/live?token=abc") == "rtsp://camera.example:554/live"


# ---------------------------------------------------------------------------
# Secrets configuration
# ---------------------------------------------------------------------------

def test_production_requires_a_non_default_long_secret():
    with pytest.raises(ValueError, match="Production requires"):
        Settings(ENVIRONMENT="production")
    with pytest.raises(ValueError, match="Production requires"):
        Settings(ENVIRONMENT="production", SECRET_KEY="short")
    configured = Settings(ENVIRONMENT="production", SECRET_KEY="k" * 48)
    assert configured.SECRET_KEY.get_secret_value() == "k" * 48


def test_wildcard_cors_is_rejected_in_every_environment():
    with pytest.raises(ValueError, match="Wildcard CORS"):
        Settings(BACKEND_CORS_ORIGINS=["*"])


def test_secret_typed_settings_do_not_leak_in_repr():
    configured = Settings(RTSP_URL=SecretStr("rtsp://user:secret@camera.invalid/live"))
    assert "secret" not in repr(configured)


def test_development_secret_key_is_only_a_named_development_default():
    assert Settings().SECRET_KEY.get_secret_value() == DEVELOPMENT_SECRET_KEY
    assert "development" in DEVELOPMENT_SECRET_KEY


# ---------------------------------------------------------------------------
# Login throttling and password handling
# ---------------------------------------------------------------------------

def test_login_rate_limiter_throttles_then_recovers():
    limiter = LoginRateLimiter(attempts=3, window_seconds=60)
    for moment in (100.0, 101.0, 102.0):
        limiter.record_failure("client-a", now=moment)
    assert limiter.is_limited("client-a", now=103.0) is True
    assert limiter.is_limited("client-b", now=103.0) is False
    assert limiter.is_limited("client-a", now=200.0) is False


def test_login_throttle_returns_429_after_repeated_failures(secured_client):
    client, _ = secured_client
    statuses = [
        client.post(
            "/api/v1/auth/login",
            json={"username": "security_admin", "password": "wrong-password"},
        ).status_code
        for _ in range(6)
    ]
    assert 429 in statuses
    assert statuses[-1] == 429
    # The correct password is still refused while the client is throttled.
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "security_admin", "password": TEST_ADMIN_PASSWORD},
    ).status_code == 429


def test_password_hashing_is_bcrypt_and_salted():
    first = hash_password(TEST_ADMIN_PASSWORD)
    second = hash_password(TEST_ADMIN_PASSWORD)
    assert first != TEST_ADMIN_PASSWORD
    assert first != second
    assert first.startswith("$2")
    assert verify_password(TEST_ADMIN_PASSWORD, first) is True
    assert verify_password("wrong-password", first) is False


def test_password_schemas_reject_values_bcrypt_cannot_represent():
    with pytest.raises(ValueError, match="at least 12"):
        UserCreate(username="operator", email="op@example.test", full_name="Op", password="short")
    with pytest.raises(ValueError, match="72 bytes"):
        UserCreate(
            username="operator",
            email="op@example.test",
            full_name="Op",
            password="x" * 73,
        )
    with pytest.raises(ValueError, match="72 bytes"):
        LoginRequest(username="operator", password="x" * 73)


# ---------------------------------------------------------------------------
# Response hygiene and authorization
# ---------------------------------------------------------------------------

def test_no_endpoint_exposes_password_hashes(secured_client):
    client, _ = secured_client
    headers = admin_headers(client)
    for path in ("/api/v1/auth/me", "/api/v1/auth/users", "/api/v1/auth/audit-logs"):
        body = client.get(path, headers=headers).text
        assert "hashed_password" not in body
        assert "$2b$" not in body
        assert "$2a$" not in body


def test_authentication_is_required_for_protected_endpoints(secured_client):
    client, _ = secured_client
    for path in (
        "/api/v1/auth/users",
        "/api/v1/auth/audit-logs",
        "/api/v1/cameras",
        "/api/v1/events",
        "/api/v1/analytics/summary",
        "/api/v1/notifications",
        "/api/v1/system/health",
        "/api/v1/system/ai-health",
        "/api/v1/system/pipelines",
        "/api/v1/configuration/plants",
    ):
        assert client.get(path).status_code in (401, 403), path


def test_response_models_exclude_internal_columns():
    assert "hashed_password" not in AnalyticsSummaryOut.model_fields
    assert "file_path" not in NotificationOut.model_fields


def test_pagination_bounds_are_enforced(secured_client):
    client, _ = secured_client
    headers = admin_headers(client)
    for path in (
        "/api/v1/auth/users?limit=0",
        "/api/v1/auth/users?limit=100000",
        "/api/v1/auth/users?offset=-1",
        "/api/v1/auth/audit-logs?limit=0",
        "/api/v1/auth/audit-logs?limit=100000",
        "/api/v1/cameras?limit=0",
        "/api/v1/cameras?limit=100000",
    ):
        assert client.get(path, headers=headers).status_code == 422, path


def test_camera_listing_is_paginated(secured_client):
    client, session = secured_client
    headers = admin_headers(client)
    for index in range(3):
        camera = Camera(
            name=f"Test camera {index}",
            code=f"CAM-{index}",
            stream_url=f"rtsp://camera.invalid/live{index}",
        )
        session.add(camera)
        session.flush()
        session.add(CameraHealth(camera_id=camera.id, status="CONFIGURED", inference_status="NOT_RUNNING"))
    session.commit()

    page = client.get("/api/v1/cameras?limit=2", headers=headers)
    assert page.status_code == 200
    assert len(page.json()) == 2
    second_page = client.get("/api/v1/cameras?limit=2&offset=2", headers=headers)
    assert len(second_page.json()) == 1


# ---------------------------------------------------------------------------
# Correlation identifiers
# ---------------------------------------------------------------------------

def test_request_id_is_generated_and_echoed(secured_client):
    client, _ = secured_client
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


def test_safe_request_id_is_preserved(secured_client):
    client, _ = secured_client
    response = client.get("/", headers={"X-Request-ID": "trace-abc_123"})
    assert response.headers["X-Request-ID"] == "trace-abc_123"


def test_unsafe_request_id_is_replaced(secured_client):
    client, _ = secured_client
    response = client.get("/", headers={"X-Request-ID": "bad value\r\ninjected"})
    assert response.headers["X-Request-ID"] != "bad value\r\ninjected"
    assert response.headers["X-Request-ID"]


# ---------------------------------------------------------------------------
# Camera configuration auditing
# ---------------------------------------------------------------------------

def test_camera_credential_urls_require_an_encryption_key(monkeypatch):
    from app.config import settings
    from app.utils.encrypted_url import validate_camera_url_storage

    monkeypatch.setattr(settings, "CAMERA_URL_ENCRYPTION_KEY", None)
    with pytest.raises(ValueError, match="CAMERA_URL_ENCRYPTION_KEY is required"):
        validate_camera_url_storage("rtsp://operator:secret@camera.example/live")
    with pytest.raises(ValueError, match="CAMERA_URL_ENCRYPTION_KEY is required"):
        validate_camera_url_storage("rtsp://camera.example/live?token=abc")
    # A credential-free source is allowed without a key.
    validate_camera_url_storage("rtsp://camera.example/live")


def test_invalid_encryption_key_is_rejected(monkeypatch):
    from app.config import settings
    from app.utils.encrypted_url import validate_camera_url_storage

    monkeypatch.setattr(settings, "CAMERA_URL_ENCRYPTION_KEY", SecretStr("not-a-fernet-key"))
    with pytest.raises(ValueError, match="not a valid Fernet key"):
        validate_camera_url_storage("rtsp://camera.example/live")


def test_credential_urls_are_encrypted_at_rest(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    from app.config import settings

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "CAMERA_URL_ENCRYPTION_KEY", SecretStr(key))
    monkeypatch.setattr(settings, "CAMERA_URL_ENCRYPTION_KEY", SecretStr(key))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        secret_url = "rtsp://operator:secret@camera.example/live?token=abc"
        camera = Camera(name="Encrypted camera", code="ENC-1", stream_url=secret_url)
        session.add(camera)
        session.commit()
        session.expire_all()
        with engine.connect() as connection:
            stored = connection.execute(
                text("select stream_url from cameras where id=:id"), {"id": camera.id}
            ).scalar_one()
        assert "secret" not in stored
        assert stored.startswith("enc:v1:")
        assert camera.stream_url == secret_url
    finally:
        session.close()
        engine.dispose()


def test_camera_audit_entries_do_not_record_stream_urls(secured_client):
    """Field names may be audited; camera source values and credentials may not."""
    client, session = secured_client
    headers = admin_headers(client)
    source = "rtsp://camera.invalid/live"
    client.post("/api/v1/cameras", json={
        "name": "Audited camera",
        "code": "AUDIT-CAM",
        "stream_url": source,
    }, headers=headers)
    entries = session.query(AuditLog).filter(AuditLog.action == "CAMERA_CREATED").all()
    assert entries
    for entry in entries:
        details = str(entry.details_json)
        assert source not in details
        assert "camera.invalid" not in details
        assert "rtsp://" not in details


def test_camera_creation_never_persists_credentials_without_encryption(monkeypatch, session):
    """A credential-bearing source must be rejected, not stored in the clear."""
    from app.config import settings
    from app.schemas_camera import CameraCreate
    from app.services.camera_manager import CameraManager

    monkeypatch.setattr(settings, "CAMERA_URL_ENCRYPTION_KEY", None)
    payload = CameraCreate(
        name="Credential camera",
        code="CRED-CAM",
        stream_url="rtsp://operator:secret@camera.example/live?token=abc",
    )
    with pytest.raises(ValueError, match="CAMERA_URL_ENCRYPTION_KEY is required"):
        CameraManager.create_camera(session, payload)
    assert session.query(Camera).count() == 0


def test_git_ignores_secrets_and_caches():
    ignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore
    assert "__pycache__/" in ignore
    assert "data/*.db" in ignore
    assert os.path.exists(Path(__file__).resolve().parents[2] / ".env.example")
