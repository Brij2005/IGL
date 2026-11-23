import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

_DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "igl_safety.db")


def _db_path() -> str:
    """Resolve the database path at call time so tests can override it via env."""
    return os.getenv("DATABASE_PATH", _DEFAULT_DB_PATH)


# Backwards-compatible module attribute (evaluated lazily by get_db_connection).
DB_PATH = os.getenv("DATABASE_PATH", _DEFAULT_DB_PATH)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_connection():
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _ensure_column(cursor, table: str, column: str, definition: str) -> None:
    rows = cursor.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row["name"] for row in rows}:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _create_tables(cursor) -> None:
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS cameras (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT,
            status TEXT DEFAULT 'DISCONNECTED',
            not_assessable_reason TEXT,
            fps REAL DEFAULT 0.0,
            latency_ms REAL DEFAULT 0.0,
            brightness REAL DEFAULT 0.0,
            blur_score REAL DEFAULT 0.0,
            resolution TEXT,
            last_seen TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS restricted_zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            zone_name TEXT NOT NULL,
            polygon_coords TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(camera_id) REFERENCES cameras(id)
        );

        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id TEXT NOT NULL,
            camera_id TEXT NOT NULL,
            zone_name TEXT,
            object_type TEXT NOT NULL,
            bounding_box TEXT NOT NULL,
            feature_vector TEXT,
            confidence REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            camera_id TEXT NOT NULL,
            camera_name TEXT,
            zone_name TEXT,
            severity TEXT NOT NULL,
            status TEXT DEFAULT 'DETECTED',
            confidence REAL NOT NULL,
            evidence_image_base64 TEXT,
            metadata_json TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            acknowledged_at TIMESTAMP,
            acknowledged_by TEXT,
            resolved_at TIMESTAMP,
            resolved_by TEXT,
            resolution_notes TEXT
        );

        CREATE TABLE IF NOT EXISTS health_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT,
            event_type TEXT,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS personnel_monitoring_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            camera_name TEXT,
            area_id TEXT DEFAULT 'Main Gate ROI',
            schedule_date TEXT DEFAULT 'ALL',
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            expected_person_count INTEGER NOT NULL DEFAULT 2,
            verification_duration INTEGER NOT NULL DEFAULT 10,
            cooldown INTEGER NOT NULL DEFAULT 60,
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS personnel_monitoring_events (
            id TEXT PRIMARY KEY,
            rule_id INTEGER,
            camera_id TEXT NOT NULL,
            camera_name TEXT,
            area_name TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expected_count INTEGER NOT NULL,
            observed_count INTEGER NOT NULL,
            additional_count INTEGER NOT NULL,
            verification_duration INTEGER NOT NULL,
            status TEXT DEFAULT 'DETECTED',
            evidence_reference TEXT,
            track_ids TEXT,
            acknowledged_at TIMESTAMP,
            acknowledged_by TEXT,
            resolved_at TIMESTAMP,
            resolved_by TEXT
        );

        CREATE TABLE IF NOT EXISTS expected_personnel_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_name TEXT NOT NULL,
            zone_name TEXT NOT NULL,
            camera_id TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            expected_person_count INTEGER NOT NULL DEFAULT 2,
            verification_duration INTEGER NOT NULL DEFAULT 10,
            cooldown INTEGER NOT NULL DEFAULT 300,
            controller_whatsapp TEXT DEFAULT '',
            whatsapp_enabled INTEGER DEFAULT 1,
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notification_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT,
            channel TEXT NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL,
            provider_message_id TEXT,
            message_text TEXT,
            error_message TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS registered_personnel (
            id TEXT PRIMARY KEY,
            track_id TEXT UNIQUE,
            name TEXT NOT NULL,
            designation TEXT DEFAULT 'Registered Worker',
            department TEXT DEFAULT 'Operations',
            face_descriptors TEXT NOT NULL DEFAULT '[]',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS plant_locations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            code TEXT,
            description TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pipelines (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            plant_location_id TEXT,
            material TEXT,
            description TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(plant_location_id) REFERENCES plant_locations(id)
        );

        CREATE TABLE IF NOT EXISTS monitoring_zones (
            id TEXT PRIMARY KEY,
            camera_id TEXT NOT NULL,
            zone_name TEXT NOT NULL,
            zone_type TEXT NOT NULL DEFAULT 'MONITORING',
            polygon_coords TEXT NOT NULL,
            color TEXT DEFAULT '#0ea5e9',
            model_ids TEXT,
            settings_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(camera_id) REFERENCES cameras(id)
        );

        CREATE TABLE IF NOT EXISTS ai_models (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            task_type TEXT NOT NULL DEFAULT 'detect',
            architecture TEXT,
            weights_path TEXT,
            classes_json TEXT,
            status TEXT NOT NULL DEFAULT 'NOT_DEPLOYED',
            active_version TEXT,
            metrics_json TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS model_versions (
            id TEXT PRIMARY KEY,
            model_id TEXT NOT NULL,
            version TEXT NOT NULL,
            weights_path TEXT,
            dataset_id TEXT,
            metrics_json TEXT,
            status TEXT NOT NULL DEFAULT 'TRAINING',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(model_id) REFERENCES ai_models(id)
        );

        CREATE TABLE IF NOT EXISTS dataset_metadata (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source TEXT,
            license TEXT,
            version TEXT,
            image_count INTEGER DEFAULT 0,
            classes_json TEXT,
            status TEXT DEFAULT 'NOT_DOWNLOADED',
            local_path TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS detection_events (
            id TEXT PRIMARY KEY,
            camera_id TEXT NOT NULL,
            plant_location_id TEXT,
            pipeline_id TEXT,
            zone_id TEXT,
            model_id TEXT,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'LOW',
            status TEXT NOT NULL DEFAULT 'DETECTED',
            confidence REAL NOT NULL DEFAULT 0.0,
            bbox_json TEXT,
            frame_timestamp TIMESTAMP,
            evidence_id TEXT,
            dedup_key TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(camera_id) REFERENCES cameras(id)
        );

        CREATE TABLE IF NOT EXISTS leakage_incidents (
            id TEXT PRIMARY KEY,
            event_id TEXT,
            camera_id TEXT NOT NULL,
            pipeline_id TEXT,
            zone_id TEXT,
            status TEXT NOT NULL DEFAULT 'POSSIBLE',
            confidence REAL NOT NULL DEFAULT 0.0,
            evidence_id TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_by TEXT,
            reviewed_at TIMESTAMP,
            review_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS safety_incidents (
            id TEXT PRIMARY KEY,
            event_id TEXT,
            camera_id TEXT NOT NULL,
            zone_id TEXT,
            category TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'DETECTED',
            confidence REAL NOT NULL DEFAULT 0.0,
            evidence_id TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_by TEXT,
            reviewed_at TIMESTAMP,
            review_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS evidence_files (
            id TEXT PRIMARY KEY,
            event_id TEXT,
            camera_id TEXT,
            file_path TEXT NOT NULL,
            content_type TEXT DEFAULT 'image/jpeg',
            width INTEGER,
            height INTEGER,
            sha256 TEXT,
            retention_days INTEGER DEFAULT 90,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT,
            event_id TEXT,
            camera_id TEXT,
            action TEXT NOT NULL,
            actor TEXT,
            details_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notification_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT,
            incident_id TEXT,
            channel TEXT NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL,
            provider_message_id TEXT,
            attempts INTEGER DEFAULT 1,
            error_message TEXT,
            sent_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_reviews (
            id TEXT PRIMARY KEY,
            event_id TEXT,
            incident_id TEXT,
            review_type TEXT NOT NULL,
            decision TEXT NOT NULL,
            notes TEXT,
            reviewer TEXT,
            is_false_positive INTEGER DEFAULT 0,
            is_false_negative INTEGER DEFAULT 0,
            reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT,
            action TEXT NOT NULL,
            resource_type TEXT,
            resource_id TEXT,
            details_json TEXT,
            ip_address TEXT,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notification_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT,
            incident_id TEXT,
            channel TEXT NOT NULL,
            recipient TEXT,
            status TEXT NOT NULL,
            provider_message_id TEXT,
            error_message TEXT,
            sent_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            report_type TEXT NOT NULL,
            period_start TIMESTAMP NOT NULL,
            period_end TIMESTAMP NOT NULL,
            format TEXT DEFAULT 'json',
            file_path TEXT,
            generated_by TEXT,
            metrics_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS asset_register (
            id TEXT PRIMARY KEY,
            asset_name TEXT NOT NULL,
            asset_category TEXT NOT NULL DEFAULT 'GENERAL',
            department TEXT,
            rfid_tag TEXT,
            barcode TEXT,
            assigned_employee_id TEXT,
            gate_pass_id TEXT,
            expected_return_date TEXT,
            status TEXT NOT NULL DEFAULT 'IN_PLANT',
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS gate_passes (
            id TEXT PRIMARY KEY,
            asset_id TEXT,
            issued_to_employee_id TEXT,
            purpose TEXT,
            expected_return_date TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP,
            metadata_json TEXT,
            FOREIGN KEY(asset_id) REFERENCES asset_register(id)
        );

        CREATE TABLE IF NOT EXISTS asset_movements (
            id TEXT PRIMARY KEY,
            asset_id TEXT,
            gate_pass_id TEXT,
            direction TEXT NOT NULL,
            gate_id TEXT,
            camera_id TEXT,
            verification_source TEXT,
            verification_status TEXT NOT NULL DEFAULT 'PENDING',
            evidence_id TEXT,
            event_id TEXT,
            metadata_json TEXT,
            moved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS access_records (
            id TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            person_id TEXT,
            person_name TEXT,
            person_type TEXT NOT NULL DEFAULT 'EMPLOYEE',
            card_id TEXT,
            gate_id TEXT,
            camera_id TEXT,
            track_id TEXT,
            verification_source TEXT,
            verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
            evidence_id TEXT,
            event_id TEXT,
            confidence REAL NOT NULL DEFAULT 0.0,
            metadata_json TEXT,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_by TEXT,
            reviewed_at TIMESTAMP,
            review_notes TEXT
        );

        CREATE TABLE IF NOT EXISTS thermal_readings (
            id TEXT PRIMARY KEY,
            camera_id TEXT NOT NULL,
            track_id TEXT,
            person_id TEXT,
            temperature_c REAL,
            threshold_c REAL,
            threshold_exceeded INTEGER DEFAULT 0,
            bbox_json TEXT,
            evidence_id TEXT,
            event_id TEXT,
            calibrated INTEGER DEFAULT 0,
            metadata_json TEXT,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS video_analyses (
            id TEXT PRIMARY KEY,
            camera_id TEXT,
            camera_name TEXT,
            source_kind TEXT NOT NULL,
            filename TEXT,
            model_id TEXT,
            frames_processed INTEGER DEFAULT 0,
            detections_count INTEGER DEFAULT 0,
            events_created INTEGER DEFAULT 0,
            people_max INTEGER DEFAULT 0,
            entry_count INTEGER DEFAULT 0,
            exit_count INTEGER DEFAULT 0,
            temperature_measurements INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'RECORDED',
            notes TEXT,
            metadata_json TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def _add_columns(cursor) -> None:
    _ensure_column(cursor, "cameras", "plant_location_id", "TEXT")
    _ensure_column(cursor, "cameras", "pipeline_id", "TEXT")
    _ensure_column(cursor, "cameras", "source_label", "TEXT")
    _ensure_column(cursor, "cameras", "enabled", "INTEGER DEFAULT 1")
    _ensure_column(cursor, "cameras", "auto_reconnect", "INTEGER DEFAULT 1")
    _ensure_column(cursor, "cameras", "reconnect_max_attempts", "INTEGER DEFAULT 5")
    _ensure_column(cursor, "cameras", "connection_timeout_ms", "INTEGER DEFAULT 8000")
    _ensure_column(cursor, "cameras", "config_json", "TEXT DEFAULT '{}'")
    _ensure_column(cursor, "cameras", "credentials_json", "TEXT DEFAULT '{}'")
    _ensure_column(cursor, "cameras", "created_at", "TIMESTAMP")
    _ensure_column(cursor, "cameras", "updated_at", "TIMESTAMP")

    _ensure_column(cursor, "alerts", "event_id", "TEXT")
    _ensure_column(cursor, "alerts", "dedup_key", "TEXT")
    _ensure_column(cursor, "alerts", "created_at", "TIMESTAMP")
    _ensure_column(cursor, "alerts", "updated_at", "TIMESTAMP")

    _ensure_column(cursor, "restricted_zones", "zone_type", "TEXT DEFAULT 'RESTRICTED'")
    _ensure_column(cursor, "restricted_zones", "model_ids", "TEXT DEFAULT '[]'")
    _ensure_column(cursor, "restricted_zones", "settings_json", "TEXT DEFAULT '{}'")
    _ensure_column(cursor, "restricted_zones", "color", "TEXT DEFAULT '#ef4444'")
    _ensure_column(cursor, "restricted_zones", "updated_at", "TIMESTAMP")

    _ensure_column(cursor, "notification_logs", "attempts", "INTEGER DEFAULT 1")
    _ensure_column(cursor, "notification_logs", "updated_at", "TIMESTAMP")


def _seed_default_data(cursor) -> None:
    cursor.execute("SELECT COUNT(*) FROM personnel_monitoring_rules")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """
            INSERT INTO personnel_monitoring_rules
            (camera_id, camera_name, area_id, schedule_date, start_time, end_time,
             expected_person_count, verification_duration, cooldown, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("Gate Camera 01", "Gate Camera 01", "Main Entry Area", "ALL", "00:00", "23:59", 2, 10, 60, 1),
        )
    else:
        cursor.execute("UPDATE personnel_monitoring_rules SET expected_person_count = 2 WHERE expected_person_count = 0")

    cursor.execute("SELECT COUNT(*) FROM expected_personnel_rules")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """
            INSERT INTO expected_personnel_rules
            (rule_name, zone_name, camera_id, start_time, end_time, expected_person_count,
             verification_duration, cooldown, controller_whatsapp, whatsapp_enabled, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("Overnight Plant Safety Rule", "Main Plant Area", "Gate Camera 01", "19:00", "06:00", 2, 10, 300, "", 1, 1),
        )

    cursor.execute("SELECT COUNT(*) FROM registered_personnel")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            """
            INSERT INTO registered_personnel (id, track_id, name, designation, department)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("EMP-101", "TRK-P101", "Shiv Kumar", "Senior Field Engineer", "Refinery Operations"),
                ("EMP-102", "TRK-P102", "Rajesh Sharma", "Plant Safety Officer", "HSE Department"),
            ],
        )

    cursor.execute("SELECT COUNT(*) FROM plant_locations")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO plant_locations (id, name, code, description) VALUES (?, ?, ?, ?)",
            ("LOC-MAIN", "Main Plant", "IGL-MAIN", "Primary manufacturing and utilities area"),
        )

    cursor.execute("SELECT COUNT(*) FROM pipelines")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO pipelines (id, name, plant_location_id, material, description) VALUES (?, ?, ?, ?, ?)",
            ("PIPE-UTIL-01", "Utilities Water Header", "LOC-MAIN", "Process water", "Main utility water pipeline"),
        )

    cursor.execute("SELECT COUNT(*) FROM ai_models")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            """
            INSERT INTO ai_models (id, name, task_type, architecture, weights_path, classes_json, status, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "model-yolov8n",
                    "YOLOv8n General Safety",
                    "detect",
                    "YOLOv8n",
                    "yolov8n.pt",
                    json.dumps({0: "person", 67: "cell phone"}),
                    "AVAILABLE",
                    json.dumps({"source": "Ultralytics", "license": "AGPL-3.0", "purpose": "general safety objects"}),
                ),
                (
                    "model-water-leakage",
                    "Water Pipeline Leakage",
                    "detect",
                    "YOLOv8 custom",
                    None,
                    json.dumps(
                        {
                            0: "Water Dripping",
                            1: "Water Spraying",
                            2: "Visible Water Flow",
                            3: "Pipe Joint Leakage",
                            4: "Water Accumulation",
                            5: "Normal/Dry Pipeline",
                        }
                    ),
                    "NOT_TRAINED",
                    json.dumps(
                        {
                            "required_dataset": "Roboflow Universe water leakage / pipeline leakage exports",
                            "status_reason": "A trained leakage model has not been supplied or trained in this repository.",
                            "never_use_coco_weights_for_leakage": True,
                        }
                    ),
                ),
            ],
        )


def _seed_smart_entry_exit(cursor) -> None:
    cursor.execute("SELECT COUNT(*) FROM ai_models WHERE id = ?", ("model-thermal-person",))
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """
            INSERT INTO ai_models (id, name, task_type, architecture, weights_path, classes_json, status, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "model-thermal-person",
                "Thermal Person Detection",
                "detect",
                "YOLOv8n (thermal fine-tune)",
                None,
                json.dumps({0: "person"}),
                "NOT_TRAINED",
                json.dumps(
                    {
                        "required_dataset": "Genuine thermal-person YOLO-format dataset export (calibrated thermal camera)",
                        "status_reason": "No thermal-trained weights are registered. Generic RGB COCO weights are never used on thermal footage.",
                        "rgb_weights_allowed_on_thermal": False,
                        "temperature_measurement_requires_calibration": True,
                    }
                ),
            ),
        )


def _seed_corrosion_model(cursor) -> None:
    """Migration 5: register the pipeline corrosion model as NOT_TRAINED."""
    cursor.execute("SELECT COUNT(*) FROM ai_models WHERE id = ?", ("model-pipe-corrosion",))
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """
            INSERT INTO ai_models (id, name, task_type, architecture, weights_path, classes_json, status, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "model-pipe-corrosion",
                "Pipeline Corrosion Detection",
                "detect",
                "YOLOv8n (corrosion fine-tune)",
                None,
                json.dumps(
                    {
                        0: "external_corrosion",
                        1: "surface_rust",
                        2: "coating_damage",
                        3: "surface_deterioration",
                        4: "normal_pipe_surface",
                    }
                ),
                "NOT_TRAINED",
                json.dumps(
                    {
                        "required_dataset": "Genuine, legally usable pipeline-corrosion YOLO-format dataset",
                        "status_reason": (
                            "No corrosion-trained weights are registered. A genuine labelled corrosion dataset "
                            "could not be acquired in this environment and no GPU is available for training. "
                            "Rust appearance alone is never treated as proof of structural failure."
                        ),
                        "blocked_by": ["corrosion_dataset_unavailable", "no_gpu_training_budget"],
                        "rgb_weights_allowed_for_corrosion": False,
                        "never_claims_structural_failure": True,
                    }
                ),
            ),
        )


def _run_migrations(cursor) -> None:
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    migrations = [
        (1, "baseline_igl_schema", _create_tables),
        (2, "camera_plant_zone_model_event_columns", _add_columns),
        (3, "default_operational_records", _seed_default_data),
        (4, "smart_entry_exit_schema", _seed_smart_entry_exit),
        (5, "pipeline_corrosion_model", _seed_corrosion_model),
    ]
    for version, name, migration in migrations:
        row = cursor.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone()
        if row:
            continue
        migration(cursor)
        cursor.execute("INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (version, name))


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    _create_tables(cursor)
    _run_migrations(cursor)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cameras_status ON cameras(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_event_type ON alerts(event_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_object_type ON observations(object_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_restricted_zones_camera_id ON restricted_zones(camera_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detection_events_camera_time ON detection_events(camera_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detection_events_type ON detection_events(event_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_evidence_event ON evidence_files(event_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_dedup ON alerts(dedup_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leakage_status ON leakage_incidents(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notification_deliveries_alert ON notification_deliveries(alert_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_access_records_direction ON access_records(direction)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_access_records_status ON access_records(verification_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_access_records_gate ON access_records(gate_id, recorded_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_asset_movements_asset ON asset_movements(asset_id, moved_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_asset_movements_status ON asset_movements(verification_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_asset_register_status ON asset_register(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_asset_register_rfid ON asset_register(rfid_tag)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_thermal_readings_camera ON thermal_readings(camera_id, recorded_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_video_analyses_kind ON video_analyses(source_kind, created_at)")

    cursor.executemany(
        "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
        [
            ("plant_name", "Plant 1", now_iso()),
            ("default_min_confidence", "0.35", now_iso()),
            ("default_min_frames", "3", now_iso()),
            ("default_cooldown_seconds", "180", now_iso()),
            ("evidence_retention_days", "90", now_iso()),
            ("camera_timeout_seconds", "8", now_iso()),
            ("leakage_dataset_requirement", "Genuine YOLO-format water leakage dataset export", now_iso()),
        ],
    )

    conn.commit()
    conn.close()


def get_setting(key: str, default=None):
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value) -> None:
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, json.dumps(value) if not isinstance(value, str) else value, now_iso()),
    )
    conn.commit()
    conn.close()


def log_audit(actor: str, action: str, resource_type: str = None, resource_id: str = None, details=None, ip_address: str = None, user_agent: str = None) -> None:
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO audit_logs (actor, action, resource_type, resource_id, details_json, ip_address, user_agent)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor,
            action,
            resource_type,
            resource_id,
            json.dumps(details or {}),
            ip_address,
            user_agent,
        ),
    )
    conn.commit()
    conn.close()


def row_to_dict(row):
    return dict(row) if row else None


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


if __name__ == "__main__":
    init_db()
    print("SQLite Database initialized at", DB_PATH)