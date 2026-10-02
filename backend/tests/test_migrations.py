"""Migration-ownership tests.

These run the real Alembic chain against temporary databases only. They assert
that the schema is migration-owned, that a fresh database upgrades and
downgrades cleanly, that existing rows survive, and that no runtime metadata
call can create or destroy schema.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "backend" / "migrations"
VERSIONS_DIR = MIGRATIONS_DIR / "versions"

sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT))

import app.models  # noqa: E402,F401
from app.database import Base, init_db  # noqa: E402

DOMAIN_TABLES = {
    "plants", "areas", "zones", "cameras", "camera_health", "roles", "users",
    "ppe_rules", "equipments", "tracks", "detections", "events", "event_evidence",
    "event_state_transitions", "incidents", "near_misses", "acknowledgements",
    "assignments", "corrective_actions", "notifications", "audit_logs",
    "model_versions", "model_metrics",
    "incident_state_transitions", "near_miss_state_transitions",
    "corrective_action_state_transitions",
    # Safety policy configuration, escalation and correlation, added in a3f6d90c2b41.
    "detector_configs", "safety_rules", "operating_thresholds",
    "escalation_policies", "event_escalations", "event_correlations",
    "notification_policies",
    # Alarm subsystem, added in b2a5c8e4f701.
    "alarms", "alarm_state_transitions",
}
BASE_REVISION = "e822fa83c9db"
DOMAIN_TABLE_COUNT = len(DOMAIN_TABLES)


def run_alembic(database_file: Path, *arguments: str) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "sqlite:///" + str(database_file).replace("\\", "/")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def domain_tables(database_file: Path) -> set[str]:
    with sqlite3.connect(database_file) as connection:
        return {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        } - {"alembic_version"}


def scalar(database_file: Path, statement: str) -> object:
    with sqlite3.connect(database_file) as connection:
        return connection.execute(statement).fetchone()[0]


def migration_files() -> list[Path]:
    return sorted(VERSIONS_DIR.glob("*.py"))


# ---------------------------------------------------------------------------
# Migration-owned schema
# ---------------------------------------------------------------------------

def test_no_migration_uses_metadata_create_or_drop_all():
    """A migration must never create or drop the schema from ORM metadata."""
    for path in migration_files():
        source = path.read_text(encoding="utf-8")
        assert "create_all" not in source, f"{path.name} calls create_all"
        assert "drop_all" not in source, f"{path.name} calls drop_all"
        assert "Base.metadata" not in source, f"{path.name} reaches into Base.metadata"


def test_runtime_init_db_refuses_to_create_schema():
    """init_db() must fail loudly instead of creating an untracked schema."""
    with pytest.raises(RuntimeError, match="no longer creates tables"):
        init_db()


def test_alembic_env_exposes_populated_target_metadata():
    """Autogenerate must see the real schema, not an empty metadata object."""
    env_source = (MIGRATIONS_DIR / "env.py").read_text(encoding="utf-8")
    assert "import app.models" in env_source
    assert "if not Base.metadata.tables" in env_source
    # And the metadata the environment relies on really is populated here.
    assert len(Base.metadata.tables) == DOMAIN_TABLE_COUNT


# ---------------------------------------------------------------------------
# Fresh database upgrade / downgrade
# ---------------------------------------------------------------------------

def test_fresh_database_upgrade_creates_every_domain_table(tmp_path):
    database_file = tmp_path / "fresh.sqlite"
    run_alembic(database_file, "upgrade", "head")
    assert domain_tables(database_file) == DOMAIN_TABLES


def test_orm_metadata_declares_exactly_the_domain_tables():
    """The ORM models and the migration chain must describe the same table set."""
    assert set(Base.metadata.tables) == DOMAIN_TABLES


def test_migrated_schema_matches_orm_metadata(tmp_path):
    """The migration chain and the ORM models must describe the same schema."""
    database_file = tmp_path / "compare.sqlite"
    run_alembic(database_file, "upgrade", "head")
    with sqlite3.connect(database_file) as connection:
        migrated = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        } - {"alembic_version", "sqlite_sequence"}
    assert migrated == set(Base.metadata.tables)


def test_fresh_database_upgrade_downgrade_upgrade(tmp_path):
    database_file = tmp_path / "roundtrip.sqlite"
    run_alembic(database_file, "upgrade", "head")
    assert len(domain_tables(database_file)) == DOMAIN_TABLE_COUNT
    run_alembic(database_file, "downgrade", "base")
    assert domain_tables(database_file) == set()
    run_alembic(database_file, "upgrade", "head")
    assert len(domain_tables(database_file)) == DOMAIN_TABLE_COUNT


def test_unmeasured_camera_health_migration_preserves_existing_rows(tmp_path):
    """Revision 007 must relax nullability without discarding data."""
    database_file = tmp_path / "preserve.sqlite"
    run_alembic(database_file, "upgrade", "b63a2f1d7c09")

    with sqlite3.connect(database_file) as connection:
        connection.execute(
            "INSERT INTO plants (id, name, code, is_active, created_at, updated_at) "
            "VALUES ('plant-1', 'Test Plant', 'TP-1', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        connection.execute(
            "INSERT INTO cameras (id, name, code, stream_url, camera_type, fps, resolution, "
            "is_active, created_at, updated_at) VALUES "
            "('cam-1', 'Test Camera', 'CAM-1', 'rtsp://camera.invalid/live', 'RTSP', 25.0, "
            "'1920x1080', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        connection.execute(
            "INSERT INTO camera_health (id, camera_id, status, fps, latency_ms, is_frozen, "
            "is_black, inference_status, health_timestamp, updated_at) VALUES "
            "('health-1', 'cam-1', 'CONFIGURED', 0.0, 0.0, 0, 0, 'NOT_RUNNING', "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        connection.commit()

    run_alembic(database_file, "upgrade", "head")

    # The row survives, and the unmeasured placeholders are now absent.
    assert scalar(database_file, "SELECT COUNT(*) FROM camera_health") == 1
    assert scalar(database_file, "SELECT status FROM camera_health WHERE id = 'health-1'") == "CONFIGURED"
    assert scalar(database_file, "SELECT fps FROM camera_health WHERE id = 'health-1'") is None
    assert scalar(database_file, "SELECT latency_ms FROM camera_health WHERE id = 'health-1'") is None
    assert scalar(database_file, "SELECT COUNT(*) FROM cameras WHERE id = 'cam-1'") == 1

    # Downgrade restores the previous non-null contract without losing the row.
    run_alembic(database_file, "downgrade", "b63a2f1d7c09")
    assert scalar(database_file, "SELECT COUNT(*) FROM camera_health") == 1
    assert scalar(database_file, "SELECT fps FROM camera_health WHERE id = 'health-1'") == 0.0
    assert scalar(database_file, "SELECT latency_ms FROM camera_health WHERE id = 'health-1'") == 0.0

    run_alembic(database_file, "upgrade", "head")
    assert scalar(database_file, "SELECT COUNT(*) FROM camera_health") == 1


def test_stepwise_upgrade_matches_upgrade_head(tmp_path):
    """Walking the chain one revision at a time must reach the same schema."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    direct = tmp_path / "direct.sqlite"
    stepwise = tmp_path / "stepwise.sqlite"
    run_alembic(direct, "upgrade", "head")

    config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    ordered_revisions = list(reversed(list(ScriptDirectory.from_config(config).walk_revisions())))
    # The chain must stay a single linear path from the original baseline, so a
    # lost or branching revision cannot quietly pass unnoticed.
    assert len({revision.revision for revision in ordered_revisions}) == len(ordered_revisions)
    assert ordered_revisions[0].revision == BASE_REVISION
    assert ScriptDirectory.from_config(config).get_heads() == [ordered_revisions[-1].revision]
    for previous, current in zip(ordered_revisions, ordered_revisions[1:]):
        assert current.down_revision == previous.revision

    for revision in ordered_revisions:
        run_alembic(stepwise, "upgrade", revision.revision)
    assert domain_tables(direct) == domain_tables(stepwise)
    assert domain_tables(stepwise) == DOMAIN_TABLES
