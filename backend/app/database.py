"""
Database configuration and session management for IGL Safety Platform.
Uses SQLAlchemy with SQLite for development; PostgreSQL-ready for production.
"""
import os
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
    """Create all tables. Safe to call repeatedly."""
    try:
        from app.models import Base as ModelsBase  # noqa: F401
    except ImportError:
        from backend.app.models import Base as ModelsBase  # noqa: F401
    Base.metadata.create_all(bind=engine)
    return engine


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable foreign keys and WAL mode for SQLite."""
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()