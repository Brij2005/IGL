"""Configuration API tests.

Authentication was deliberately removed from this platform, so these tests no
longer mint access tokens. They assert only what the endpoints actually return.
"""
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.database import Base, get_db
from app.main import app, seed_reference_roles
from app.models import Area, Plant, Role, User


def _session():
    """Build an isolated in-memory database. Never touches data/database.db."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    seed_reference_roles(session)
    role = session.query(Role).filter_by(name="ADMIN").one()
    admin = User(
        username="config_test_admin",
        email="config-admin@example.test",
        full_name="Configuration Test Admin",
        role_id=role.id,
    )
    session.add(admin)
    session.commit()
    return engine, session


def test_configuration_api_accepts_only_submitted_data_and_marks_ppe_unvalidated():
    engine, session = _session()

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            plant_response = client.post(
                "/api/v1/configuration/plants",
                json={"name": "TEST_ONLY plant", "code": "TEST_ONLY_PLANT"},
            )
            assert plant_response.status_code == 201
            area_response = client.post(
                "/api/v1/configuration/areas",
                json={"plant_id": plant_response.json()["id"], "name": "TEST_ONLY area", "code": "TEST_ONLY_AREA"},
            )
            assert area_response.status_code == 201
            zone_response = client.post(
                "/api/v1/configuration/zones",
                json={
                    "area_id": area_response.json()["id"],
                    "name": "TEST_ONLY zone",
                    "code": "TEST_ONLY_ZONE",
                    "zone_type": "WORK_AREA",
                    "geometry_json": [[0, 0], [1, 0], [1, 1]],
                },
            )
            assert zone_response.status_code == 201
            ppe_response = client.post(
                "/api/v1/configuration/ppe-rules",
                json={"zone_id": zone_response.json()["id"], "ppe_type": "TEST_ONLY_ITEM", "is_mandatory": True},
            )
            assert ppe_response.status_code == 201
            # Confidence and validation must not be invented for an unvalidated rule.
            assert ppe_response.json()["min_confidence"] is None
            assert ppe_response.json()["threshold_source"] == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
            assert ppe_response.json()["validation_status"] == "NOT_VALIDATED"
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_configuration_api_rejects_invalid_polygon():
    engine, session = _session()
    plant = Plant(name="TEST_ONLY plant", code="BAD_TEST_PLANT")
    session.add(plant)
    session.flush()
    area = Area(plant_id=plant.id, name="TEST_ONLY area", code="BAD_TEST_AREA")
    session.add(area)
    session.commit()

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/configuration/zones",
                json={
                    "area_id": area.id,
                    "name": "TEST_ONLY bad zone",
                    "code": "BAD_TEST_ZONE",
                    "zone_type": "RESTRICTED",
                    # Two points cannot form a polygon.
                    "geometry_json": [[0, 0], [1, 1]],
                },
            )
            assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()