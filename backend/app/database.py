"""
Database configuration and session management for IGL Safety Platform.

Uses SQLAlchemy with SQLite for development; PostgreSQL-ready for production.

The engine is configured from validated settings only. There is no runtime
schema creation: the Alembic chain owns the schema, and startup refuses to serve
a database that is not at the migration head.
"""
import os
from typing import Generator

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import declarative_base, sessionmaker, Session

try:
    from app.config import settings
except ImportError:
    from backend.app.config import settings


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

DATABASE_URL = settings.DATABASE_URL.get_secret_value()
database_url = make_url(DATABASE_URL)
IS_SQLITE = database_url.drivername.startswith("sqlite")

if IS_SQLITE and database_url.database not in (None, ":memory:"):
    if not os.path.isabs(database_url.database):
        DATABASE_URL = database_url.set(
            database=os.path.join(PROJECT_ROOT, database_url.database)
        ).render_as_string(hide_password=False)

connect_args: dict = {}
engine_kwargs: dict = {}
if IS_SQLITE:
    connect_args["check_same_thread"] = False
    connect_args["timeout"] = settings.DATABASE_CONNECT_TIMEOUT_SECONDS
else:
    engine_kwargs.update(
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_recycle=settings.DATABASE_POOL_RECYCLE_SECONDS,
        pool_pre_ping=True,
    )
    if database_url.drivername.startswith("postgresql"):
        connect_args["connect_timeout"] = settings.DATABASE_CONNECT_TIMEOUT_SECONDS
        engine_kwargs["statement_timeout"] = settings.DATABASE_STATEMENT_TIMEOUT_MS
    elif database_url.drivername.startswith("mysql"):
        connect_args["connect_timeout"] = settings.DATABASE_CONNECT_TIMEOUT_SECONDS

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=settings.SQLALCHEMY_ECHO,
    future=True,
    **engine_kwargs,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
Base = declarative_base()


@event.listens_for(Engine, "connect")
def _set_connection_pragmas(dbapi_connection, connection_record):
    """Apply safety pragmas to every SQLite connection in the process.

    Registered on the Engine class rather than one engine instance so that
    test, migration, and application connections all enforce foreign keys
    identically. SQLite ignores foreign keys unless they are enabled per
    connection, so an unconfigured connection would silently accept orphaned
    rows.
    """
    if not IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        if connection_record.dbapi_connection is not None:
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            except Exception:  # noqa: BLE001 - in-memory databases reject WAL
                pass
    finally:
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    """Yield a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Refuse implicit schema creation.

    Schema is owned exclusively by the Alembic migrations. Creating tables from
    ORM metadata at runtime would create an untracked, partially migrated schema
    that the startup head check cannot reason about, so this helper exists only
    to fail loudly for any caller that still expects ``create_all`` behaviour.
    """
    raise RuntimeError(
        "init_db() no longer creates tables. Schema is migration-owned; run "
        "'alembic -c backend/alembic.ini upgrade head' instead."
    )


def _alembic_config() -> Config:
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    migrations_dir = os.path.join(backend_dir, "migrations")
    versions_dir = os.path.join(migrations_dir, "versions")
    config = Config(os.path.join(backend_dir, "alembic.ini"))
    config.set_main_option("script_location", migrations_dir)
    config.set_main_option("version_locations", versions_dir)
    return config


def get_migration_state() -> tuple[set[str], set[str]]:
    """Return the (expected, current) Alembic revision heads for this engine."""
    expected_heads = set(ScriptDirectory.from_config(_alembic_config()).get_heads())
    with engine.connect() as connection:
        current_heads = set(MigrationContext.configure(connection).get_current_heads())
    return expected_heads, current_heads


def migration_state_detail() -> dict:
    """Describe migration state without raising, for readiness reporting."""
    try:
        expected_heads, current_heads = get_migration_state()
    except Exception as exc:  # noqa: BLE001 - health output must never raise
        return {
            "state": "MIGRATIONS_UNAVAILABLE",
            "expected_heads": [],
            "current_heads": [],
            "error_type": type(exc).__name__,
        }
    if not current_heads:
        state = "MIGRATIONS_MISSING"
    elif current_heads == expected_heads:
        state = "MIGRATIONS_CURRENT"
    else:
        state = "MIGRATIONS_PENDING"
    return {
        "state": state,
        "expected_heads": sorted(expected_heads),
        "current_heads": sorted(current_heads),
        "error_type": None,
    }


def require_database_at_migration_head() -> None:
    """Refuse to operate on a schema that has not been explicitly migrated."""
    expected_heads, current_heads = get_migration_state()
    if current_heads != expected_heads:
        raise RuntimeError(
            "Database migration is missing or out of date "
            f"(database={sorted(current_heads) or 'none'}, code={sorted(expected_heads)}); run "
            "'alembic -c backend/alembic.ini upgrade head' before starting the API"
        )


def database_reachable() -> bool:
    """Return True when a trivial query succeeds. Never raises."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - readiness output must never raise
        return False
