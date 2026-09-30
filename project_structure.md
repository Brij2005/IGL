# Project Structure

This document describes the **actual** contents of this repository. Files that
are planned but not present are not listed. Each entry is marked with its
current state.

Status vocabulary used throughout this repository:

- `IMPLEMENTED` — present, exercised by tests, and behaves as described.
- `PARTIAL` — present and usable, but missing a capability that the platform
  needs for operational use.
- `UNCONFIGURED` — implemented, but no real value has been supplied.
- `NOT_VALIDATED` — implemented, but never measured against authorized real input.
- `BLOCKED_BY_REAL_INPUT` — cannot be completed because no authorized real
  input exists in this workspace.

```
.
├── README.md                    # Honest current-status summary
├── project_structure.md         # This file
├── .env.example                 # Template only; contains no secret
├── .gitignore                   # Excludes .env, caches, and SQLite files
├── ai_models/
│   ├── base_model.py            # IMPLEMENTED  Abstract model contract
│   ├── detection.py             # IMPLEMENTED  Validated Detection structure
│   └── ultralytics_adapter.py   # IMPLEMENTED  Explicit-path adapter; never downloads
├── backend/
│   ├── alembic.ini              # Alembic configuration
│   ├── requirements.txt         # Pinned dependencies
│   ├── migrations/
│   │   ├── env.py               # IMPLEMENTED  Loads ORM metadata for autogenerate
│   │   └── versions/            # IMPLEMENTED  7 deterministic revisions
│   │       ├── e822fa83c9db_001_initial_schema_22_tables.py
│   │       ├── c14a2b9f6d31_002_observed_camera_health.py
│   │       ├── d71e3f0a2c44_003_nullable_unmeasured_image_quality.py
│   │       ├── a08d5e9c1f72_004_event_state_transitions.py
│   │       ├── f2c8a6d104be_005_widen_encrypted_camera_url.py
│   │       ├── b63a2f1d7c09_006_ppe_rule_provenance.py
│   │       └── 9c4b2e7a1d55_007_nullable_unmeasured_camera_telemetry.py
│   ├── app/
│   │   ├── main.py              # IMPLEMENTED  App, lifespan, request-ID middleware
│   │   ├── config.py            # IMPLEMENTED  Settings, production guards
│   │   ├── database.py          # IMPLEMENTED  Engine/session; schema is migration-owned
│   │   ├── models.py            # IMPLEMENTED  23 domain tables
│   │   ├── auth.py              # IMPLEMENTED  bcrypt, JWT, RBAC, login throttle
│   │   ├── schemas.py           # IMPLEMENTED  Auth/user/audit request+response models
│   │   ├── schemas_camera.py    # IMPLEMENTED  Camera models; stream URL sanitized
│   │   ├── schemas_events.py    # IMPLEMENTED  Event read/transition models
│   │   ├── schemas_configuration.py  # IMPLEMENTED  Plant/area/zone/PPE models
│   │   ├── schemas_system.py    # IMPLEMENTED  Response models for system endpoints
│   │   ├── api/
│   │   │   ├── __init__.py      # IMPLEMENTED  Router registry
│   │   │   ├── auth.py          # IMPLEMENTED  Login, users, roles, audit logs
│   │   │   ├── cameras.py       # IMPLEMENTED  Camera CRUD + health
│   │   │   ├── events.py        # IMPLEMENTED  Event list + workflow transitions
│   │   │   ├── evidence.py      # IMPLEMENTED  Event-linked evidence retrieval
│   │   │   ├── analytics.py     # IMPLEMENTED  Database count summary
│   │   │   ├── notifications.py # IMPLEMENTED  In-app queue
│   │   │   ├── system_health.py # IMPLEMENTED  Health, AI health, pipelines
│   │   │   └── configuration.py # IMPLEMENTED  Plant/area/zone/PPE configuration
│   │   ├── services/
│   │   │   ├── inference_pipeline.py   # IMPLEMENTED  Frame -> model -> tracking
│   │   │   ├── video_ingestion.py      # PARTIAL  RTSP/file reader; no real source tested
│   │   │   ├── tracker.py              # IMPLEMENTED  Visual-only IoU tracker
│   │   │   ├── camera_manager.py       # IMPLEMENTED  Camera CRUD and health row
│   │   │   ├── health_monitor.py       # PARTIAL  Diagnostic evaluation from real frames
│   │   │   ├── continuous_health.py    # IMPLEMENTED  Polling camera-health worker
│   │   │   ├── system_health.py        # IMPLEMENTED  Derived subsystem states
│   │   │   ├── evidence_engine.py      # PARTIAL  Snapshot capture from buffered frames
│   │   │   ├── notification_engine.py  # PARTIAL  Queue only; external senders absent
│   │   │   ├── workflow_engine.py      # IMPLEMENTED  Validated state transitions
│   │   │   ├── zone_engine.py          # PARTIAL  Geometry primitives; no IGL polygons
│   │   │   └── ppe_rules.py            # PARTIAL  Rule interpretation primitive
│   │   ├── engine/
│   │   │   └── temporal_verifier.py    # PARTIAL  Standalone primitive; not wired to events
│   │   └── utils/
│   │       ├── frame_buffer.py   # IMPLEMENTED  Bounded frame buffer
│   │       ├── encrypted_url.py  # IMPLEMENTED  Fernet camera-URL encryption
│   │       └── redaction.py       # IMPLEMENTED  Shared credential redaction
│   └── tests/                    # IMPLEMENTED  See docs/api.md for counts
│       ├── conftest.py
│       ├── test_auth.py
│       ├── test_bootstrap_admin.py
│       ├── test_camera_ingestion.py
│       ├── test_camera_url_encryption.py
│       ├── test_configuration_api.py
│       ├── test_continuous_health.py
│       ├── test_database.py
│       ├── test_evidence_engine.py
│       ├── test_health_semantics.py
│       ├── test_migrations.py
│       ├── test_notifications_analytics.py
│       ├── test_phase4_core.py
│       ├── test_phase4_pipeline.py
│       ├── test_safety_primitives.py
│       ├── test_security.py
│       ├── test_startup.py
│       ├── test_validation_runner.py
│       └── test_workflow_engine.py
├── config/igl/                  # UNCONFIGURED  Empty schema-shaped templates only
│   ├── README.md
│   ├── areas.yaml, cameras.yaml, equipment.yaml, escalation_rules.yaml
│   ├── ppe_rules.yaml, plants.yaml, safety_rules.yaml, zones.yaml
├── data/                        # Git-ignored runtime data
│   └── database.db              # Local development database (not committed)
├── docs/
│   ├── ai_validation.md
│   ├── api.md
│   ├── architecture.md
│   ├── igl_configuration.md
│   ├── operations.md
│   ├── security.md
│   ├── setup.md
│   ├── troubleshooting.md
│   └── validation.md
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   └── js/app.js                # PARTIAL  Dashboard for the existing APIs
└── scripts/
    ├── bootstrap_admin.py       # IMPLEMENTED  One-time, operator-supplied admin
    └── run_inference.py         # IMPLEMENTED  Real-input validation runner
```

## Not Present

The following modules are referenced by no import in this repository and are
listed here so they are not mistaken for missing work: safety event generation,
PPE detection, proximity, fall, fire/smoke, and leakage detection; face
recognition or identity resolution; WebSocket or live-video streaming; external
notification senders (email, webhook); an import workflow for `config/igl/`;
incident, near-miss, acknowledgement, assignment, and corrective-action write
APIs (their tables exist and are read by analytics, but no write endpoint
exists).

## Explicitly Absent Data

No authorized IGL camera footage, plant layout, PPE SOP dataset, model
checkpoint, or labeled IGL validation dataset is present in this workspace.
Nothing in this repository substitutes for them.
