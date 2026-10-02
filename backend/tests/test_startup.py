"""Application startup and shutdown behaviour.

The app must start only against a migrated schema, must not create schema or an
administrator at startup, and must report honest state through the public
endpoints once running.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base, get_migration_state
from app.main import app, seed_reference_roles
from app.models import Role, User


def test_configured_test_database_is_at_migration_head():
    expected, current = get_migration_state()
    assert current == expected, "the test database must be migrated by conftest"


def test_application_starts_and_stops_cleanly():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
    # A second startup must also be clean, proving shutdown released the workers.
    with TestClient(app) as client:
        assert client.get("/").status_code == 200


def test_startup_does_not_create_an_administrator():
    with TestClient(app):
        pass
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        assert session.query(User).count() == 0
    finally:
        session.close()


def test_startup_seeds_only_fixed_platform_roles():
    with TestClient(app):
        pass
    from app.database import SessionLocal
    from app.models import Role

    session = SessionLocal()
    try:
        assert {role.name for role in session.query(Role).all()} == {
            "ADMIN", "SAFETY_OFFICER", "PLANT_MANAGER", "OPERATOR"
        }
    finally:
        session.close()


def test_reference_role_seeding_preserves_existing_configuration():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(Role(
            name="SAFETY_OFFICER",
            description="Operator-maintained description",
            permissions_json=["operator:maintained"],
        ))
        session.commit()

        seed_reference_roles(session)

        role = session.query(Role).filter(Role.name == "SAFETY_OFFICER").one()
        assert role.description == "Operator-maintained description"
        assert role.permissions_json == ["operator:maintained"]
        assert {item.name for item in session.query(Role).all()} == {
            "ADMIN", "SAFETY_OFFICER", "PLANT_MANAGER", "OPERATOR"
        }
    finally:
        session.close()
        engine.dispose()


def test_startup_refuses_an_unmigrated_database(monkeypatch):
    import app.main as main_module

    def raise_error():
        raise RuntimeError("Database migration is missing or out of date")

    monkeypatch.setattr(main_module, "require_database_at_migration_head", raise_error)
    with pytest.raises(RuntimeError, match="migration is missing or out of date"):
        with TestClient(app):
            pass


def test_every_route_responds_after_startup():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/v1/openapi.json").status_code == 200
        # This build has no authentication, so these routes are reachable. The
        # test records that deliberately permissive behaviour rather than
        # asserting a protection that this build does not have.
        assert client.get("/api/v1/system/health").status_code == 200
        assert client.get("/api/v1/analytics/summary").status_code == 200
        # A removed route must be gone, not silently absent.
        assert client.post("/api/v1/auth/login", json={}).status_code == 404


def test_openapi_documents_every_registered_router():
    with TestClient(app) as client:
        paths = client.get("/api/v1/openapi.json").json()["paths"]
    for prefix in (
        "/api/v1/cameras", "/api/v1/events", "/api/v1/analytics/summary",
        "/api/v1/notifications", "/api/v1/system/health", "/api/v1/configuration/plants",
        "/api/v1/events/{event_id}/evidence", "/api/v1/cameras/webcam/devices"
    ):
        assert any(path.startswith(prefix) for path in paths), prefix
    # Authentication routes must not reappear in the API document.
    assert not any(path.startswith("/api/v1/auth") for path in paths)
