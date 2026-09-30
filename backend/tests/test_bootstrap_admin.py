"""Tests for the one-time, operator-supplied first-admin bootstrap.

Every value used here is an explicit test fixture created inside the test's own
in-memory database. No production or operator database is touched, and no
default administrator credential exists anywhere in the code under test.
"""
from pathlib import Path
import sys

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import verify_password
from app.config import Settings
from app.database import Base
from app.models import AuditLog, Role, User
from scripts import bootstrap_admin


TEST_PASSWORD = "test-only-admin-password"


@pytest.fixture
def session():
    """An isolated in-memory database for a single bootstrap test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def bootstrap_env(monkeypatch):
    """Default the bootstrap configuration to disabled with no values set."""
    monkeypatch.setattr(bootstrap_admin.settings, "BOOTSTRAP_ADMIN_ENABLED", False)
    monkeypatch.setattr(bootstrap_admin.settings, "BOOTSTRAP_ADMIN_USERNAME", None)
    monkeypatch.setattr(bootstrap_admin.settings, "BOOTSTRAP_ADMIN_EMAIL", None)
    monkeypatch.setattr(bootstrap_admin.settings, "BOOTSTRAP_ADMIN_FULL_NAME", None)
    monkeypatch.setattr(bootstrap_admin.settings, "BOOTSTRAP_ADMIN_PASSWORD", None)
    return monkeypatch


def enable_configured_bootstrap(monkeypatch, **overrides):
    values = {
        "BOOTSTRAP_ADMIN_ENABLED": True,
        "BOOTSTRAP_ADMIN_USERNAME": "configured_admin",
        "BOOTSTRAP_ADMIN_EMAIL": "configured_admin@example.test",
        "BOOTSTRAP_ADMIN_FULL_NAME": "Configured Administrator",
        "BOOTSTRAP_ADMIN_PASSWORD": SecretStr(TEST_PASSWORD),
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(bootstrap_admin.settings, name, value)


def bypass_migration_check(monkeypatch, session):
    monkeypatch.setattr(bootstrap_admin, "require_database_at_migration_head", lambda: None)
    monkeypatch.setattr(bootstrap_admin, "SessionLocal", lambda: session)


def seed_existing_user(session):
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


# ---------------------------------------------------------------------------
# Disabled bootstrap
# ---------------------------------------------------------------------------

def test_disabled_bootstrap_creates_no_administrator(session, bootstrap_env, capsys):
    bypass_migration_check(bootstrap_env, session)
    assert bootstrap_admin.main(["--configured"]) == bootstrap_admin.REFUSAL_DISABLED
    assert session.query(User).count() == 0
    assert "No administrator was created" in capsys.readouterr().err


def test_settings_reject_enabled_bootstrap_without_operator_values():
    with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_ENABLED requires operator-supplied values"):
        Settings(BOOTSTRAP_ADMIN_ENABLED=True)


def test_settings_allow_enabled_bootstrap_with_complete_values():
    configured = Settings(
        BOOTSTRAP_ADMIN_ENABLED=True,
        BOOTSTRAP_ADMIN_USERNAME="operator_admin",
        BOOTSTRAP_ADMIN_EMAIL="operator@example.test",
        BOOTSTRAP_ADMIN_FULL_NAME="Operator Admin",
        BOOTSTRAP_ADMIN_PASSWORD=SecretStr(TEST_PASSWORD),
    )
    assert configured.BOOTSTRAP_ADMIN_ENABLED is True


def test_disabled_bootstrap_is_the_default():
    assert Settings().BOOTSTRAP_ADMIN_ENABLED is False
    assert Settings().BOOTSTRAP_ADMIN_PASSWORD is None


# ---------------------------------------------------------------------------
# Enabled bootstrap
# ---------------------------------------------------------------------------

def test_enabled_bootstrap_creates_one_bcrypt_hashed_administrator(session, bootstrap_env, capsys):
    enable_configured_bootstrap(bootstrap_env)
    bypass_migration_check(bootstrap_env, session)

    assert bootstrap_admin.main(["--configured"]) == bootstrap_admin.SUCCESS

    user = session.query(User).filter(User.username == "configured_admin").one()
    assert user.role.name == "ADMIN"
    assert user.is_active is True
    assert user.hashed_password != TEST_PASSWORD
    assert user.hashed_password.startswith("$2")
    assert verify_password(TEST_PASSWORD, user.hashed_password)
    audit = session.query(AuditLog).filter(AuditLog.action == "INITIAL_ADMIN_BOOTSTRAPPED").one()
    assert audit.resource_id == user.id
    output = capsys.readouterr()
    assert TEST_PASSWORD not in output.out
    assert TEST_PASSWORD not in output.err


def test_repeated_bootstrap_is_refused_and_idempotent(session, bootstrap_env, capsys):
    enable_configured_bootstrap(bootstrap_env)
    bypass_migration_check(bootstrap_env, session)

    assert bootstrap_admin.main(["--configured"]) == bootstrap_admin.SUCCESS
    assert bootstrap_admin.main(["--configured"]) == bootstrap_admin.REFUSAL_ALREADY_INITIALIZED
    assert "a user already exists" in capsys.readouterr().err
    assert session.query(User).count() == 1


def test_bootstrap_is_refused_when_any_non_admin_user_exists(session, bootstrap_env, capsys):
    seed_existing_user(session)
    enable_configured_bootstrap(bootstrap_env)
    bypass_migration_check(bootstrap_env, session)

    assert bootstrap_admin.main(["--configured"]) == bootstrap_admin.REFUSAL_ALREADY_INITIALIZED
    assert session.query(User).count() == 1
    assert session.query(User).filter(User.username == "configured_admin").count() == 0


# ---------------------------------------------------------------------------
# Invalid bootstrap configuration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"BOOTSTRAP_ADMIN_PASSWORD": SecretStr("short")}, "at least 12 characters"),
        ({"BOOTSTRAP_ADMIN_PASSWORD": SecretStr("x" * 73)}, "bcrypt 72-byte limit"),
        ({"BOOTSTRAP_ADMIN_PASSWORD": SecretStr("configured_admin")}, "must differ from the username"),
        ({"BOOTSTRAP_ADMIN_EMAIL": "not-an-email"}, "valid administrator email"),
        ({"BOOTSTRAP_ADMIN_USERNAME": "   "}, "username is required"),
        ({"BOOTSTRAP_ADMIN_FULL_NAME": ""}, "full name is required"),
        ({"BOOTSTRAP_ADMIN_PASSWORD": None}, "password is required"),
    ],
)
def test_invalid_bootstrap_configuration_creates_nothing(session, bootstrap_env, overrides, expected_message):
    enable_configured_bootstrap(bootstrap_env, **overrides)
    bypass_migration_check(bootstrap_env, session)

    with pytest.raises(bootstrap_admin.BootstrapConfigurationError, match=expected_message):
        bootstrap_admin.configured_bootstrap_values()
    assert session.query(User).count() == 0


def test_interactive_bootstrap_rejects_mismatched_confirmation():
    with pytest.raises(bootstrap_admin.BootstrapConfigurationError, match="confirmation does not match"):
        bootstrap_admin.validate_bootstrap_values(
            "interactive_admin",
            "interactive@example.test",
            "Interactive Administrator",
            TEST_PASSWORD,
            "test-only-different-password",
        )


def test_interactive_bootstrap_creates_one_admin_without_echoing_password(session, bootstrap_env, capsys):
    prompts = iter(["test_admin", "admin@example.test", "Test Administrator"])
    passwords = iter([TEST_PASSWORD, TEST_PASSWORD])
    monkeypatch = bootstrap_env
    monkeypatch.setattr(bootstrap_admin, "require_database_at_migration_head", lambda: None)
    monkeypatch.setattr(bootstrap_admin, "SessionLocal", lambda: session)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))
    monkeypatch.setattr(bootstrap_admin, "getpass", lambda prompt: next(passwords))

    assert bootstrap_admin.main() == bootstrap_admin.SUCCESS
    user = session.query(User).filter(User.username == "test_admin").one()
    assert user.role.name == "ADMIN"
    assert verify_password(TEST_PASSWORD, user.hashed_password)
    assert TEST_PASSWORD not in capsys.readouterr().out


def test_interactive_bootstrap_refuses_when_any_user_exists(session, bootstrap_env, capsys):
    seed_existing_user(session)
    monkeypatch = bootstrap_env
    monkeypatch.setattr(bootstrap_admin, "require_database_at_migration_head", lambda: None)
    monkeypatch.setattr(bootstrap_admin, "SessionLocal", lambda: session)

    assert bootstrap_admin.main() == bootstrap_admin.REFUSAL_ALREADY_INITIALIZED
    assert "a user already exists" in capsys.readouterr().err


def test_bootstrap_refuses_when_the_database_is_not_migrated(session, bootstrap_env, capsys):
    def raise_migration_error():
        raise RuntimeError("Database migration is missing or out of date")

    bootstrap_env.setattr(bootstrap_admin, "require_database_at_migration_head", raise_migration_error)
    bootstrap_env.setattr(bootstrap_admin, "SessionLocal", lambda: session)
    enable_configured_bootstrap(bootstrap_env)

    assert bootstrap_admin.main(["--configured"]) == bootstrap_admin.REFUSAL_MIGRATION
    assert session.query(User).count() == 0
    assert "migration is missing" in capsys.readouterr().err


def test_unknown_bootstrap_argument_is_rejected(session, bootstrap_env, capsys):
    bypass_migration_check(bootstrap_env, session)
    assert bootstrap_admin.main(["--create-default-admin"]) == bootstrap_admin.REFUSAL_INVALID_CONFIGURATION
    assert session.query(User).count() == 0
    assert "Unknown bootstrap argument" in capsys.readouterr().err
