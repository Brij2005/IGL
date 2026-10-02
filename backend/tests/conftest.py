"""Test session isolation.

The test suite must never read, write, migrate, or drop the operator's
configured database. A temporary file database is created, migrated with the
real Alembic migration chain, and exported through ``DATABASE_URL`` before any
application module is imported, so every test exercises a migration-owned
schema that no local deployment depends on.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"

for _import_path in (str(PROJECT_ROOT), str(BACKEND_ROOT)):
    if _import_path not in sys.path:
        sys.path.insert(0, _import_path)


_TEMP_ROOT = Path(tempfile.mkdtemp(prefix="igl_test_env_"))
_TEST_DATABASE = _TEMP_ROOT / "test_database.db"
_TEST_DATABASE_URL = "sqlite:///" + str(_TEST_DATABASE).replace("\\", "/")

# Set before app.config is imported anywhere, so the settings singleton and the
# engine are bound to the temporary database.
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
os.environ["EVIDENCE_DIR"] = str(_TEMP_ROOT / "evidence")
os.environ["ENVIRONMENT"] = "test"
for _model_variable in (
    "VIDEO_SOURCE",
    "RTSP_URL",
    "MODEL_WEIGHTS_PATH",
    "MODEL_NAME",
    "MODEL_VERSION",
    "CAMERA_URL_ENCRYPTION_KEY",
):
    os.environ.pop(_model_variable, None)


def _alembic(*arguments: str) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = _TEST_DATABASE_URL
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def pytest_sessionstart(session):
    """Migrate the temporary test database with the real migration chain."""
    result = _alembic("upgrade", "head")
    if result.returncode != 0:
        raise RuntimeError("Test database migration failed:\n" + result.stdout + result.stderr)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEMP_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def test_database_path() -> Path:
    return _TEST_DATABASE


@pytest.fixture(autouse=True)
def reset_temporal_verification_registry():
    """Clear in-process safety state so tests cannot leak observations into each other.

    There is no login throttle to reset any more: this build has no
    authentication and therefore no failed-login counter.
    """
    from app.services.safety_engine import temporal_registry

    temporal_registry.reset()
    yield
    temporal_registry.reset()
