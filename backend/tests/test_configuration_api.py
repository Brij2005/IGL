from pathlib import Path
import sys

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import create_access_token, hash_password
from app.database import Base, get_db
from app.main import app, seed_default_roles
from app.models import Role, User


def test_configuration_api_accepts_only_submitted_data_and_marks_ppe_unvalidated():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    seed_default_roles(session)
    role = session.query(Role).filter_by(name="ADMIN").one()
    admin = User(
        username="config_test_admin",
        email="config-admin@example.test",
        full_name="Configuration Test Admin",
        hashed_password=hash_password("test-only-password"),
        role_id=role.id,
    )
    session.add(admin)
    session.commit()
    token = create_access_token({"sub": admin.username, "user_id": admin.id, "role": "ADMIN"})

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {token}"}
            plant_response = client.post(
                "/api/v1/configuration/plants",
                json={"name": "TEST_ONLY plant", "code": "TEST_ONLY_PLANT"},
                headers=headers,
            )
            assert plant_response.status_code == 201
            area_response = client.post(
                "/api/v1/configuration/areas",
                json={"plant_id": plant_response.json()["id"], "name": "TEST_ONLY area", "code": "TEST_ONLY_AREA"},
                headers=headers,
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
                headers=headers,
            )
            assert zone_response.status_code == 201
            ppe_response = client.post(
                "/api/v1/configuration/ppe-rules",
                json={"zone_id": zone_response.json()["id"], "ppe_type": "TEST_ONLY_ITEM", "is_mandatory": True},
                headers=headers,
            )
            assert ppe_response.status_code == 201
            assert ppe_response.json()["min_confidence"] is None
            assert ppe_response.json()["threshold_source"] == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
            assert ppe_response.json()["validation_status"] == "NOT_VALIDATED"
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_configuration_api_rejects_invalid_polygon():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    seed_default_roles(session)
    role = session.query(Role).filter_by(name="ADMIN").one()
    admin = User(
        username="invalid_config_test_admin",
        email="invalid-config-admin@example.test",
        full_name="Invalid Configuration Test Admin",
        hashed_password=hash_password("test-only-password"),
        role_id=role.id,
    )
    from app.models import Plant, Area
    plant = Plant(name="TEST_ONLY plant", code="BAD_TEST_PLANT")
    session.add(plant)
    session.flush()
    area = Area(plant_id=plant.id, name="TEST_ONLY area", code="BAD_TEST_AREA")
    session.add_all([admin, area])
    session.commit()
    token = create_access_token({"sub": admin.username, "user_id": admin.id, "role": "ADMIN"})

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
                    "geometry_json": [[0, 0], [1, 1]],
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
