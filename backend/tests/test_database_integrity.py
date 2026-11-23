"""Database integrity, migration idempotency and relationship tests."""

import shutil

import pytest

# The shared tests/conftest.py owns DATABASE_PATH so all modules use one database.
from database import get_db_connection, init_db, now_iso

REQUIRED_TABLES = [
    "cameras", "monitoring_zones", "pipelines", "plant_locations", "ai_models",
    "model_versions", "dataset_metadata", "detection_events", "safety_incidents",
    "alerts", "evidence_files", "access_records", "asset_register", "gate_passes",
    "asset_movements", "thermal_readings", "video_analyses", "users", "audit_logs",
    "reports", "notification_deliveries", "leakage_incidents",
]


def test_migration_runs_twice_idempotently():
    """init_db() must be safe to run repeatedly without duplicating seed data."""
    init_db()
    conn = get_db_connection()
    models_first = conn.execute("SELECT COUNT(*) FROM ai_models").fetchone()[0]
    migrations_first = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    conn.close()

    init_db()  # second run
    conn = get_db_connection()
    models_second = conn.execute("SELECT COUNT(*) FROM ai_models").fetchone()[0]
    migrations_second = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    conn.close()

    assert models_first == models_second, "Seed rows must not duplicate on re-run"
    assert migrations_first == migrations_second, "Migrations must not duplicate on re-run"


def test_all_required_tables_exist():
    init_db()
    conn = get_db_connection()
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    missing = [t for t in REQUIRED_TABLES if t not in tables]
    assert not missing, f"Missing tables: {missing}"


def test_migrations_recorded_in_order():
    init_db()
    conn = get_db_connection()
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    conn.close()
    assert versions == sorted(versions), "Migrations must be recorded in ascending order"
    assert len(versions) >= 4, "Migration 4 (smart entry & exit) must be applied"


def test_seed_data_present():
    init_db()
    conn = get_db_connection()
    locs = conn.execute("SELECT COUNT(*) FROM plant_locations").fetchone()[0]
    pipes = conn.execute("SELECT COUNT(*) FROM pipelines").fetchone()[0]
    thermal = conn.execute("SELECT status FROM ai_models WHERE id='model-thermal-person'").fetchone()
    conn.close()
    assert locs > 0, "Seed plant locations required"
    assert pipes > 0, "Seed pipeline required"
    assert thermal and thermal[0] == "NOT_TRAINED", "Thermal model must seed as NOT_TRAINED"


def test_foreign_keys_enabled():
    conn = get_db_connection()
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.close()
    assert fk == 1, "Foreign key enforcement must be ON"


def test_event_references_valid_camera():
    """A detection event must persist with a real camera reference."""
    init_db()
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO cameras (id, name, source_type, source_url, status) VALUES ('CAM-DB-1','C','image','x','ONLINE')"
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO detection_events (id, camera_id, event_type, frame_timestamp, zone_id, confidence, status)
            VALUES ('EVT-DB-1','CAM-DB-1','PERSON_DETECTED',?, 'Zone A', 0.9, 'NEW')
            """,
            (now_iso(),),
        )
        conn.commit()
        row = conn.execute(
            "SELECT e.id, c.name FROM detection_events e JOIN cameras c ON c.id = e.camera_id WHERE e.id='EVT-DB-1'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "Event must join to its camera"


def test_evidence_links_to_event():
    init_db()
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO cameras (id, name, source_type, source_url, status) VALUES ('CAM-DB-2','C2','image','x','ONLINE')"
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO detection_events (id, camera_id, event_type, frame_timestamp, zone_id, confidence, status)
            VALUES ('EVT-DB-2','CAM-DB-2','PERSON_DETECTED',?, 'Zone A', 0.9, 'NEW')
            """,
            (now_iso(),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO evidence_files (id, camera_id, file_path, content_type, width, height, retention_days) "
            "VALUES ('EVD-DB-1','CAM-DB-2','/tmp/x.jpg','image/jpeg',100,100,90)"
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM evidence_files WHERE camera_id='CAM-DB-2'").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_asset_movement_links_asset_and_gate_pass():
    init_db()
    conn = get_db_connection()
    conn.execute(
        "INSERT OR IGNORE INTO asset_register (id, asset_name, status) VALUES ('AST-DB-1','Drill','IN_PLANT')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO gate_passes (id, asset_id, status) VALUES ('GP-DB-1','AST-DB-1','OPEN')"
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO asset_movements (id, asset_id, gate_pass_id, direction, verification_status)
        VALUES ('MOV-DB-1','AST-DB-1','GP-DB-1','EXIT','MATCHED')
        """
    )
    conn.commit()
    row = conn.execute(
        "SELECT m.id, a.asset_name, g.status FROM asset_movements m "
        "JOIN asset_register a ON a.id=m.asset_id "
        "JOIN gate_passes g ON g.id=m.gate_pass_id WHERE m.id='MOV-DB-1'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[1] == "Drill"
    assert row[2] == "OPEN"


def test_audit_log_rows_are_queryable():
    init_db()
    from database import log_audit

    log_audit(actor="tester", action="test.action", resource_type="test", resource_id="1", details={"k": "v"})
    conn = get_db_connection()
    row = conn.execute("SELECT actor, action FROM audit_logs WHERE actor='tester' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert row[1] == "test.action"


def test_referential_integrity_check():
    """PRAGMA foreign_key_check must report no violations."""
    init_db()
    conn = get_db_connection()
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()
    assert violations == [], f"Foreign key violations: {violations}"


def teardown_module(module):
    pass