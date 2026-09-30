"""Create the first administrator interactively; no default credentials exist."""
from getpass import getpass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from app.auth import hash_password
from app.database import SessionLocal
from app.main import require_database_at_migration_head, seed_default_roles
from app.models import AuditLog, Role, User


def main() -> int:
    try:
        require_database_at_migration_head()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        if db.query(User.id).first() is not None:
            print("Bootstrap refused: a user already exists.", file=sys.stderr)
            return 2
        seed_default_roles(db)
        admin_role = db.query(Role).filter(Role.name == "ADMIN").first()
        if admin_role is None:
            print("Bootstrap failed: migrate the database and seed platform roles first.", file=sys.stderr)
            return 2

        username = input("Administrator username: ").strip()
        email = input("Administrator email: ").strip()
        full_name = input("Administrator full name: ").strip()
        password = getpass("New administrator password (minimum 12 characters): ")
        confirmation = getpass("Confirm administrator password: ")
        if not username or not email or not full_name:
            print("Username, email, and full name are required.", file=sys.stderr)
            return 2
        if len(password) < 12 or password != confirmation:
            print("Password confirmation failed or password is shorter than 12 characters.", file=sys.stderr)
            return 2

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
            details_json={"bootstrap_method": "interactive_one_time_cli"},
        ))
        db.commit()
        print("Initial administrator created. Remove bootstrap access and protect the account credentials.")
        return 0
    except Exception:
        db.rollback()
        print("Administrator bootstrap failed; no credentials were logged.", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
