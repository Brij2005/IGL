from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import verify_password
from app.database import Base
from app.models import Role, User
from scripts import bootstrap_admin
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def create_test_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine)()


def test_interactive_bootstrap_creates_one_admin_without_echoing_password(monkeypatch, capsys):
    engine, session = create_test_session()
    prompts = iter(["test_admin", "admin@example.test", "Test Administrator"])
    passwords = iter(["test-only-password-123", "test-only-password-123"])
    monkeypatch.setattr(bootstrap_admin, "require_database_at_migration_head", lambda: None)
    monkeypatch.setattr(bootstrap_admin, "SessionLocal", lambda: session)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))
    monkeypatch.setattr(bootstrap_admin, "getpass", lambda prompt: next(passwords))
    try:
        assert bootstrap_admin.main() == 0
        user = session.query(User).filter(User.username == "test_admin").one()
        assert user.role.name == "ADMIN"
        assert verify_password("test-only-password-123", user.hashed_password)
        assert "test-only-password-123" not in capsys.readouterr().out
    finally:
        session.close()
        engine.dispose()


def test_interactive_bootstrap_refuses_when_any_user_exists(monkeypatch, capsys):
    engine, session = create_test_session()
    role = Role(name="OPERATOR")
    session.add(role)
    session.flush()
    session.add(User(
        username="existing_test_user",
        email="existing@example.test",
        full_name="Existing Test User",
        hashed_password="test-only-hash",
        role_id=role.id,
    ))
    session.commit()
    monkeypatch.setattr(bootstrap_admin, "require_database_at_migration_head", lambda: None)
    monkeypatch.setattr(bootstrap_admin, "SessionLocal", lambda: session)
    try:
        assert bootstrap_admin.main() == 2
        assert "a user already exists" in capsys.readouterr().err
    finally:
        session.close()
        engine.dispose()