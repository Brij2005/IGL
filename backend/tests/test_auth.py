"""
Automated unit & integration tests for Phase 2 Authentication and RBAC.
Tests login success/failure, token validation, /auth/me, role authorization,
password hash protection, user management, and security audit logging.
"""
import pytest
import sys
import os
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base, get_db
from app.models import User, Role, AuditLog
from app.auth import hash_password, create_access_token, LoginRateLimiter
from app.main import app, seed_default_roles


@pytest.fixture
def test_db():
    """Create an isolated in-memory SQLite database session for auth tests."""
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///file:auth_test_db?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False, "uri": True},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    
    # Seed default RBAC roles
    seed_default_roles(session)
    
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(test_db):
    """FastAPI TestClient with overridden get_db dependency."""
    def override_get_db():
        try:
            yield test_db
        finally:
            pass
            
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def setup_users(test_db):
    """Create controlled test users for each role."""
    admin_role = test_db.query(Role).filter(Role.name == "ADMIN").first()
    operator_role = test_db.query(Role).filter(Role.name == "OPERATOR").first()
    manager_role = test_db.query(Role).filter(Role.name == "PLANT_MANAGER").first()
    
    admin_user = User(
        username="admin_test",
        email="admin@igl.test",
        hashed_password=hash_password("AdminPass123!"),
        full_name="System Administrator",
        employee_code="EMP-ADMIN-01",
        role_id=admin_role.id,
        is_active=True
    )
    
    operator_user = User(
        username="operator_test",
        email="operator@igl.test",
        hashed_password=hash_password("OperatorPass123!"),
        full_name="Plant Operator",
        employee_code="EMP-OP-01",
        role_id=operator_role.id,
        is_active=True
    )

    manager_user = User(
        username="manager_test",
        email="manager@igl.test",
        hashed_password=hash_password("ManagerPass123!"),
        full_name="Plant Manager",
        employee_code="EMP-MGR-01",
        role_id=manager_role.id,
        is_active=True
    )
    
    test_db.add_all([admin_user, operator_user, manager_user])
    test_db.commit()
    
    return {
        "admin": admin_user,
        "operator": operator_user,
        "manager": manager_user
    }


def test_successful_login(client, setup_users):
    """Verify successful login returns valid JWT token and user details."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin_test", "password": "AdminPass123!"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["username"] == "admin_test"
    assert data["role"] == "ADMIN"


def test_invalid_password_rejection(client, setup_users, test_db):
    """Verify invalid password returns HTTP 401 and logs audit failure event."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin_test", "password": "WrongPassword!"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"
    
    # Audit log check
    audit_log = test_db.query(AuditLog).filter(AuditLog.action == "LOGIN_FAILED").first()
    assert audit_log is not None
    assert audit_log.details_json["username"] == "admin_test"


def test_unknown_user_rejection(client, setup_users):
    """Verify non-existent username returns HTTP 401."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "non_existent_user", "password": "AnyPassword!"}
    )
    assert response.status_code == 401


def test_expired_or_invalid_token_rejection(client):
    """Verify invalid or expired JWT token is rejected with HTTP 401."""
    # Test invalid token string
    headers = {"Authorization": "Bearer invalid.jwt.token"}
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401

    # Test expired token
    expired_token = create_access_token(
        data={"sub": "admin_test", "user_id": "test_id", "role": "ADMIN"},
        expires_delta=timedelta(seconds=-10)
    )
    headers_expired = {"Authorization": f"Bearer {expired_token}"}
    response_expired = client.get("/api/v1/auth/me", headers=headers_expired)
    assert response_expired.status_code == 401


def test_auth_me_endpoint(client, setup_users):
    """Verify GET /auth/me returns current user details."""
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "operator_test", "password": "OperatorPass123!"}
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["username"] == "operator_test"
    assert user_data["email"] == "operator@igl.test"
    assert user_data["role"]["name"] == "OPERATOR"


def test_password_hash_not_exposed(client, setup_users):
    """CRITICAL SECURITY RULE: Verify password hash is never exposed in API responses."""
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin_test", "password": "AdminPass123!"}
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    response = client.get("/api/v1/auth/me", headers=headers)
    user_data = response.json()
    assert "hashed_password" not in user_data
    assert "password" not in user_data


def test_role_authorization_admin_access(client, setup_users):
    """Verify ADMIN role can create a new user."""
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin_test", "password": "AdminPass123!"}
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    new_user_payload = {
        "username": "new_safety_officer",
        "email": "officer@igl.test",
        "password": "OfficerPass123!",
        "full_name": "Safety Officer John",
        "employee_code": "EMP-SO-01",
        "role_name": "SAFETY_OFFICER"
    }
    
    response = client.post("/api/v1/auth/users", json=new_user_payload, headers=headers)
    assert response.status_code == 201
    created_user = response.json()
    assert created_user["username"] == "new_safety_officer"
    assert created_user["role"]["name"] == "SAFETY_OFFICER"


def test_unauthorized_role_access_rejection(client, setup_users):
    """Verify OPERATOR role is forbidden (HTTP 403) from creating users."""
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "operator_test", "password": "OperatorPass123!"}
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    new_user_payload = {
        "username": "unauthorized_create",
        "email": "hacker@igl.test",
        "password": "HackerPass123!",
        "full_name": "Hacker User",
        "role_name": "ADMIN"
    }
    
    response = client.post("/api/v1/auth/users", json=new_user_payload, headers=headers)
    assert response.status_code == 403
    assert "does not have required permissions" in response.json()["detail"]


def test_security_audit_logging(client, setup_users, test_db):
    """Verify security-sensitive actions generate audit log entries."""
    # Perform login
    client.post(
        "/api/v1/auth/login",
        json={"username": "admin_test", "password": "AdminPass123!"}
    )
    
    # Query audit logs in DB
    login_logs = test_db.query(AuditLog).filter(AuditLog.action == "LOGIN_SUCCESS").all()
    assert len(login_logs) >= 1
    assert login_logs[0].details_json["username"] == "admin_test"


def test_login_rate_limiter_expires_failures_and_clears_on_success():
    limiter = LoginRateLimiter(attempts=2, window_seconds=10)
    limiter.record_failure("test-client", now=100)
    assert limiter.is_limited("test-client", now=105) is False
    limiter.record_failure("test-client", now=106)
    assert limiter.is_limited("test-client", now=107) is True
    assert limiter.is_limited("test-client", now=111) is False
    limiter.record_failure("test-client", now=112)
    limiter.clear("test-client")
    assert limiter.is_limited("test-client", now=112) is False
