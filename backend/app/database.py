"""
Database configuration and session management for IGL Safety Platform.
Uses SQLAlchemy with SQLite for development; PostgreSQL-ready for production.
"""
import os
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Generator

try:
    from app.config import settings
except ImportError:
    from backend.app.config import settings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = settings.DATABASE_URL
database_url = make_url(DATABASE_URL)
if database_url.drivername.startswith("sqlite") and database_url.database not in (None, ":memory:"):
    if not os.path.isabs(database_url.database):
        DATABASE_URL = database_url.set(
            database=os.path.join(PROJECT_ROOT, database_url.database)
        ).render_as_string(hide_password=False)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    poolclass=StaticPool if DATABASE_URL.startswith("sqlite") else None,
    echo=settings.SQLALCHEMY_ECHO,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


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


def get_migration_state() -> tuple[set[str], set[str]]:
    """Return the (expected, current) Alembic revision heads for this engine."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    alembic_config = Config(os.path.join(backend_dir, "alembic.ini"))
    alembic_config.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
    expected_heads = set(ScriptDirectory.from_config(alembic_config).get_heads())
    with engine.connect() as connection:
        current_heads = set(MigrationContext.configure(connection).get_current_heads())
    return expected_heads, current_heads


def require_database_at_migration_head() -> None:
    """Refuse to operate on a schema that has not been explicitly migrated."""
    expected_heads, current_heads = get_migration_state()
    if current_heads != expected_heads:
        raise RuntimeError(
            "Database migration is missing or out of date; run "
            "'alembic -c backend/alembic.ini upgrade head' before starting the API"
        )


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable foreign keys and WAL mode for SQLite."""
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()