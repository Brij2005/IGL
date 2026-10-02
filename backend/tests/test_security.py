"""Security regression tests.

These tests cover credential redaction, authentication and role enforcement,
encryption requirements for credential-bearing camera URLs, audit records that
never contain camera source values, response headers and request-ID
sanitisation, pagination bounds, response-model hygiene, anonymous-access
configuration, and repository secret hygiene.

Every credential used here is a test fixture created inside the test; no real
secret, camera, or production database is involved.
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

from app.access_control import (
    ANONYMOUS_ACCESS_STATE,
    access_state,
    log_audit_event,
    require_permission,
    require_role,
    resolve_actor,
)
from app.config import Settings
from app.database import Base, get_db
from app.main import app, seed_reference_roles
from app.models import AuditLog, Camera, CameraHealth, Role, User
from app.schemas import IdentityCreate
from app.schemas_camera import CameraOut, sanitize_stream_url
from app.schemas_system import AnalyticsSummaryOut, NotificationOut
from app.utils.redaction import display_source_identifier, safe_source_identifier


@pytest.fixture
def session():
    """An isolated in-memory database shared across request threads."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///file:security_test_db?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False, "uri": True},
        poolclass=StaticPool
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
    """A TestClient served from an isolated test database."""
    seed_reference_roles(session)
    admin_role = session.query(Role).filter(Role.name == "ADMIN").one()
    session.add(User(
        username="security_admin",
        email="security_admin@example.test",
        full_name="Security Test Administrator",
        role_id=admin_role.id,
        is_active=True
    ))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, session
    app.dependency_overrides.clear()


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
    ]
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
    ]
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

def test_shared_deployment_requires_a_signing_secret():
    with pytest.raises(ValueError, match="AUTH_JWT_SECRET_KEY"):
        Settings(
            ENVIRONMENT="production",
            ALLOW_ANONYMOUS_ACCESS=False,
            DATABASE_URL="postgresql://operator:secret@database.example/igl",
            _env_file=None,
        )


def test_wildcard_cors_is_rejected_in_every_environment():
    with pytest.raises(ValueError, match="Wildcard CORS"):
        Settings(BACKEND_CORS_ORIGINS=["*"])


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_non_local_environment_rejects_anonymous_access(environment):
    with pytest.raises(ValueError, match="Anonymous access is only permitted"):
        Settings(ENVIRONMENT=environment, _env_file=None)


def test_non_local_environment_can_fail_closed_when_anonymous_access_is_disabled():
    configured = Settings(
        ENVIRONMENT="production",
        ALLOW_ANONYMOUS_ACCESS=False,
        AUTH_JWT_SECRET_KEY=SecretStr("x" * 32),
        DATABASE_URL="postgresql://operator:secret@database.example/igl",
        _env_file=None,
    )
    assert configured.authentication_state() == "ANONYMOUS_ACCESS_DISABLED_AUTHENTICATION_REQUIRED"


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_non_local_environment_rejects_sqlite_database(environment):
    with pytest.raises(ValueError, match="SQLite is local-development only"):
        Settings(
            ENVIRONMENT=environment,
            ALLOW_ANONYMOUS_ACCESS=False,
            AUTH_JWT_SECRET_KEY=SecretStr("x" * 32),
            _env_file=None,
        )


def test_secret_typed_settings_do_not_leak_in_repr():
    configured = Settings(
        RTSP_URL=SecretStr("rtsp://user:camera-secret@camera.invalid/live"),
        DATABASE_URL="postgresql://operator:database-secret@database.invalid/igl",
        SMTP_USERNAME="operator",
        SMTP_PASSWORD=SecretStr("smtp-secret"),
        WHATSAPP_ACCESS_TOKEN=SecretStr("whatsapp-secret"),
    )
    representation = repr(configured)
    for secret in ("camera-secret", "database-secret", "smtp-secret", "whatsapp-secret"):
        assert secret not in representation


def test_access_model_is_reported_explicitly():
    assert Settings(ALLOW_ANONYMOUS_ACCESS=True).authentication_state() == (
        "ANONYMOUS_ACCESS_ENABLED_NO_AUTHENTICATION"
    )
    assert Settings(ALLOW_ANONYMOUS_ACCESS=False, AUTH_JWT_SECRET_KEY=SecretStr("y" * 32)).authentication_state() == (
        "ANONYMOUS_ACCESS_DISABLED_AUTHENTICATION_REQUIRED"
    )


# ---------------------------------------------------------------------------
# Access control with no authentication
# ---------------------------------------------------------------------------

def test_resolve_actor_uses_only_verified_request_credentials(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ALLOW_ANONYMOUS_ACCESS", True)
    assert resolve_actor(None, None) is None
    assert access_state(None) == ANONYMOUS_ACCESS_STATE


def test_declared_requirements_are_kept_visible():
    """Endpoint requirements stay declared even though nothing can be checked."""
    permission_dependency = require_permission("cameras:view")
    role_dependency = require_role("ADMIN", "SAFETY_OFFICER")
    assert permission_dependency.declared_permission == "cameras:view"
    assert role_dependency.declared_roles == ("ADMIN", "SAFETY_OFFICER")


def test_disabling_anonymous_access_refuses_instead_of_pretending(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ALLOW_ANONYMOUS_ACCESS", False)
    with pytest.raises(Exception) as refusal:
        require_permission("cameras:view")(None)
    assert "Unmet requirement: cameras:view" in str(refusal.value)
    with pytest.raises(Exception) as role_refusal:
        require_role("ADMIN")(None)
    assert "Unmet requirement: ADMIN" in str(role_refusal.value)
    monkeypatch.setattr(settings, "ALLOW_ANONYMOUS_ACCESS", True)
    assert require_permission("cameras:view")(None) is None


def test_endpoints_refuse_when_anonymous_access_is_disabled(secured_client, monkeypatch):
    from app.config import settings

    client, _ = secured_client
    monkeypatch.setattr(settings, "ALLOW_ANONYMOUS_ACCESS", False)
    for path in (
        "/api/v1/cameras",
        "/api/v1/events",
        "/api/v1/analytics/summary",
        "/api/v1/notifications",
        "/api/v1/configuration/plants",
    ):
        assert client.get(path).status_code == 401, path
    # Health deliberately stays reachable so monitoring still works when
    # access is closed. It returns only derived state and never mutates anything.
    assert client.get("/api/v1/system/health").status_code == 200


def test_endpoints_are_reachable_while_anonymous_access_is_enabled(secured_client):
    """Records the current, deliberately permissive behaviour of this build."""
    client, _ = secured_client
    for path in (
        "/api/v1/cameras",
        "/api/v1/events",
        "/api/v1/analytics/summary",
        "/api/v1/notifications",
        "/api/v1/system/health",
    ):
        assert client.get(path).status_code == 200, path


def test_authentication_endpoints_exist_and_reject_empty_credentials(secured_client):
    client, _ = secured_client
    assert client.post("/api/v1/auth/login", json={}).status_code == 422
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_token_authenticates_user_and_enforces_role(secured_client, monkeypatch):
    import bcrypt
    from pydantic import SecretStr
    from app.config import settings

    client, db = secured_client
    user = db.query(User).filter(User.username == "security_admin").one()
    user.hashed_password = bcrypt.hashpw(b"correct-horse-battery", bcrypt.gensalt()).decode("ascii")
    user.password_changed_at = datetime.now(timezone.utc)
    db.commit()
    monkeypatch.setattr(settings, "AUTH_JWT_SECRET_KEY", SecretStr("test-signing-key-" * 3))
    monkeypatch.setattr(settings, "ALLOW_ANONYMOUS_ACCESS", False)

    assert client.get("/api/v1/identity/users").status_code == 401
    response = client.post("/api/v1/auth/login", json={"username_or_email": "security_admin", "password": "correct-horse-battery"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=headers).json()["username"] == "security_admin"
    assert client.get("/api/v1/identity/users", headers=headers).status_code == 200
    assert client.get("/api/v1/identity/users", headers={"Authorization": "Bearer invalid"}).status_code == 401
    changed = client.post(
        "/api/v1/auth/password",
        json={"current_password": "correct-horse-battery", "new_password": "a-different-secure-password"},
        headers=headers,
    )
    assert changed.status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"username_or_email": "security_admin", "password": "a-different-secure-password"},
    ).status_code == 200


def test_role_restriction_rejects_authenticated_non_admin(secured_client, monkeypatch):
    import bcrypt
    from pydantic import SecretStr
    from app.config import settings

    client, db = secured_client
    operator_role = db.query(Role).filter(Role.name == "SAFETY_OFFICER").one()
    operator = User(
        username="safety_user",
        email="safety_user@example.test",
        full_name="Safety User",
        role_id=operator_role.id,
        hashed_password=bcrypt.hashpw(b"safety-user-password", bcrypt.gensalt()).decode("ascii"),
        password_changed_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db.add(operator)
    db.commit()
    monkeypatch.setattr(settings, "AUTH_JWT_SECRET_KEY", SecretStr("test-signing-key-" * 3))
    monkeypatch.setattr(settings, "ALLOW_ANONYMOUS_ACCESS", False)
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username_or_email": "safety_user", "password": "safety-user-password"},
    )
    assert login_response.status_code == 200
    headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}
    assert client.get("/api/v1/events", headers=headers).status_code == 200
    denied = client.post(
        "/api/v1/identity/users",
        json={"username": "another-user", "email": "another@example.test", "full_name": "Another User", "password": "another-secure-password", "role_name": "OPERATOR"},
        headers=headers,
    )
    assert denied.status_code == 403


def test_audit_rows_record_anonymous_access_rather_than_an_identity(session):
    entry = log_audit_event(
        db=session,
        user_id=None,
        action="TEST_ANONYMOUS_ACTION",
        resource_type="CAMERA",
        resource_id="camera-1",
    )
    assert entry.user_id is None
    assert entry.details_json["access_state"] == ANONYMOUS_ACCESS_STATE


# ---------------------------------------------------------------------------
# Response hygiene
# ---------------------------------------------------------------------------

def test_response_models_exclude_internal_columns():
    assert "hashed_password" not in AnalyticsSummaryOut.model_fields
    assert "file_path" not in NotificationOut.model_fields


def test_legacy_credential_column_is_never_populated():
    """Account writes store a hash while public responses never expose it."""
    column = User.__table__.columns["hashed_password"]
    assert column.nullable is True
    from app.schemas import IdentityOut
    assert "password" in IdentityCreate.model_fields
    assert "hashed_password" not in IdentityOut.model_fields
    assert "password" not in IdentityOut.model_fields


def test_pagination_bounds_are_enforced(secured_client):
    client, _ = secured_client
    for path in (
        "/api/v1/cameras?limit=0",
        "/api/v1/cameras?limit=100000",
        "/api/v1/cameras?offset=-1",
        "/api/v1/events?limit=0",
        "/api/v1/events?limit=100000",
    ):
        assert client.get(path).status_code == 422, path


def test_camera_listing_is_paginated(secured_client):
    client, session = secured_client
    for index in range(3):
        camera = Camera(
            name=f"Test camera {index}",
            code=f"CAM-{index}",
            stream_url=f"rtsp://camera.invalid/live{index}"
        )
        session.add(camera)
        session.flush()
        session.add(CameraHealth(camera_id=camera.id, status="CONFIGURED", inference_status="NOT_RUNNING"))
    session.commit()

    page = client.get("/api/v1/cameras?limit=2")
    assert page.status_code == 200
    assert len(page.json()) == 2
    second_page = client.get("/api/v1/cameras?limit=2&offset=2")
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


def test_baseline_security_headers_and_auth_cache_policy(secured_client):
    client, _session = secured_client
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == "camera=(self), microphone=(), geolocation=()"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers

    login = client.post("/api/v1/auth/login", json={})
    assert login.headers["Cache-Control"] == "no-store"


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


def test_credential_urls_are_encrypted_at_rest(monkeypatch):
    from cryptography.fernet import Fernet

    from app.config import settings

    key = Fernet.generate_key().decode("ascii")
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
    source = "rtsp://camera.invalid/live"
    client.post("/api/v1/cameras", json={
        "name": "Audited camera",
        "code": "AUDIT-CAM",
        "stream_url": source,
    })
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
        stream_url="rtsp://operator:secret@camera.example/live?token=abc"
    )
    with pytest.raises(ValueError, match="CAMERA_URL_ENCRYPTION_KEY is required"):
        CameraManager.create_camera(session, payload)
    assert session.query(Camera).count() == 0


def test_git_ignores_secrets_and_caches():
    ignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore
    assert "__pycache__/" in ignore
    assert "*.db" in ignore
    assert "*.sqlite3" in ignore
    assert "*.log" in ignore
    assert "data/evidence/" in ignore
    assert "*.pt" in ignore
    assert "*.onnx" in ignore
    assert "*.engine" in ignore
    assert os.path.exists(Path(__file__).resolve().parents[2] / ".env.example")
