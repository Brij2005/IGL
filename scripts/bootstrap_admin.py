"""Create the first administrator.

Two mutually exclusive modes are supported and neither has a default credential:

* interactive: prompts for every value and never echoes the password;
* configured: only runs when ``BOOTSTRAP_ADMIN_ENABLED`` is true and every
  ``BOOTSTRAP_ADMIN_*`` value was supplied by the operator.

Bootstrap is one-time. It refuses to run when any user already exists, so a
repeat invocation is a no-op rather than a second administrator. Startup never
invokes it: if bootstrap configuration is absent, no administrator is created.
"""
from getpass import getpass
from pathlib import Path
import sys
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from app.auth import hash_password
from app.config import settings
from app.database import SessionLocal, require_database_at_migration_head
from app.main import seed_default_roles
from app.models import AuditLog, Role, User


REFUSAL_MIGRATION = 2
REFUSAL_ALREADY_INITIALIZED = 2
REFUSAL_DISABLED = 2
REFUSAL_INVALID_CONFIGURATION = 2
FAILURE = 1
SUCCESS = 0


class BootstrapConfigurationError(ValueError):
    """Raised when bootstrap is requested without complete operator configuration."""


def validate_bootstrap_values(
    username: str,
    email: str,
    full_name: str,
    password: str,
    password_confirmation: str | None = None,
) -> None:
    """Reject incomplete or weak bootstrap input before any database write."""
    if not username or not username.strip():
        raise BootstrapConfigurationError("An administrator username is required")
    if not email or "@" not in email:
        raise BootstrapConfigurationError("A valid administrator email is required")
    if not full_name or not full_name.strip():
        raise BootstrapConfigurationError("An administrator full name is required")
    if not password:
        raise BootstrapConfigurationError("An administrator password is required")
    if len(password) < settings.MINIMUM_ADMIN_PASSWORD_LENGTH:
        raise BootstrapConfigurationError(
            f"Administrator password must be at least {settings.MINIMUM_ADMIN_PASSWORD_LENGTH} characters"
        )
    # bcrypt silently ignores bytes past 72; reject rather than truncate.
    if len(password.encode("utf-8")) > 72:
        raise BootstrapConfigurationError("Administrator password exceeds the bcrypt 72-byte limit")
    if password.strip().lower() == username.strip().lower():
        raise BootstrapConfigurationError("Administrator password must differ from the username")
    if password_confirmation is not None and password != password_confirmation:
        raise BootstrapConfigurationError("Administrator password confirmation does not match")


def configured_bootstrap_values() -> tuple[str, str, str, str] | None:
    """Return operator-supplied bootstrap values, or None when not enabled."""
    if not settings.BOOTSTRAP_ADMIN_ENABLED:
        return None
    try:
        values = (
            settings.BOOTSTRAP_ADMIN_USERNAME or "",
            settings.BOOTSTRAP_ADMIN_EMAIL or "",
            settings.BOOTSTRAP_ADMIN_FULL_NAME or "",
            settings.BOOTSTRAP_ADMIN_PASSWORD.get_secret_value() if settings.BOOTSTRAP_ADMIN_PASSWORD else "",
        )
    except AttributeError as exc:  # pragma: no cover - defensive
        raise BootstrapConfigurationError("Bootstrap administrator password is not configured") from exc
    validate_bootstrap_values(*values)
    return values


def prompt_bootstrap_values() -> tuple[str, str, str, str]:
    username = input("Administrator username: ").strip()
    email = input("Administrator email: ").strip()
    full_name = input("Administrator full name: ").strip()
    password = getpass(
        f"New administrator password (minimum {settings.MINIMUM_ADMIN_PASSWORD_LENGTH} characters): "
    )
    confirmation = getpass("Confirm administrator password: ")
    return username, email, full_name, password, confirmation


class BootstrapAlreadyInitialized(RuntimeError):
    """Raised when any user already exists."""


def create_initial_admin(db, *, username: str, email: str, full_name: str, password: str) -> User:
    """Create the single initial administrator, or refuse when one already exists."""
    existing_user = db.query(User.id).first()
    if existing_user is not None:
        raise BootstrapAlreadyInitialized("Bootstrap refused: a user already exists.")

    seed_default_roles(db)
    admin_role = db.query(Role).filter(Role.name == "ADMIN").first()
    if admin_role is None:
        raise BootstrapConfigurationError("Platform ADMIN role is missing; migrate and seed roles first")

    user = User(
        username=username,
        email=email,
        full_name=full_name,
        hashed_password=hash_password(password),
        role_id=admin_role.id,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(AuditLog(
        user_id=user.id,
        action="INITIAL_ADMIN_BOOTSTRAPPED",
        resource_type="USER",
        resource_id=user.id,
        details_json={"bootstrap_method": "one_time_operator_supplied"},
    ))
    db.commit()
    db.refresh(user)
    return user


def main(argv: Sequence[str] | None = None, *, interactive: bool = True) -> int:
    """Bootstrap the first administrator.

    ``--configured`` selects the non-interactive path, which runs only when
    ``BOOTSTRAP_ADMIN_ENABLED`` is true and every value was supplied by the
    operator. Without that flag the interactive prompt is used, which requires
    a controlled terminal and echoes nothing.
    """
    argv = list(argv or ())
    known = {"--interactive", "--configured"}
    unknown = [item for item in argv if item not in known]
    if unknown:
        print(f"Unknown bootstrap argument: {unknown[0]}", file=sys.stderr)
        return REFUSAL_INVALID_CONFIGURATION
    if "--configured" in argv:
        interactive = False

    try:
        require_database_at_migration_head()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return REFUSAL_MIGRATION

    db = SessionLocal()
    try:
        # Refuse before prompting, so an operator is never asked for a
        # password that would be discarded.
        if db.query(User.id).first() is not None:
            print("Bootstrap refused: a user already exists.", file=sys.stderr)
            return REFUSAL_ALREADY_INITIALIZED

        if not interactive:
            try:
                configured = configured_bootstrap_values()
            except BootstrapConfigurationError as exc:
                print(str(exc), file=sys.stderr)
                return REFUSAL_INVALID_CONFIGURATION
            if configured is None:
                print(
                    "Bootstrap refused: BOOTSTRAP_ADMIN_ENABLED is not set. No administrator was created.",
                    file=sys.stderr,
                )
                return REFUSAL_DISABLED
            username, email, full_name, password = configured
        else:
            try:
                username, email, full_name, password, confirmation = prompt_bootstrap_values()
                validate_bootstrap_values(username, email, full_name, password, confirmation)
            except BootstrapConfigurationError as exc:
                print(str(exc), file=sys.stderr)
                return REFUSAL_INVALID_CONFIGURATION

        create_initial_admin(db, username=username, email=email, full_name=full_name, password=password)
        print("Initial administrator created. Remove bootstrap configuration and protect the credentials.")
        return SUCCESS
    except BootstrapAlreadyInitialized as exc:
        db.rollback()
        print(str(exc), file=sys.stderr)
        return REFUSAL_ALREADY_INITIALIZED
    except Exception:
        db.rollback()
        print("Administrator bootstrap failed; no credentials were logged.", file=sys.stderr)
        return FAILURE
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
