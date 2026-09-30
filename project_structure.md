# IGL Industrial AI Safety & Incident Intelligence Platform

## Project Structure

```
igl-safety-platform/
├── README.md
├── ARCHITECTURE.md
├── INSTALLATION.md
├── .env.example
├── config/                    # IGL configuration templates
│   ├── plants.yaml
│   ├── zones.yaml
│   ├── ppe_rules.yaml
│   └── safety_thresholds.yaml
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI entry point
│   │   ├── database.py         # SQLAlchemy setup
│   │   ├── models.py           # All ORM models
│   │   ├── schemas.py          # Pydantic schemas
│   │   ├── config.py           # Config loader
│   │   ├── auth.py             # RBAC
│   │   ├── api/                # Route modules
│   │   │   ├── __init__.py
│   │   │   ├── auth.py
│   │   │   ├── cameras.py
│   │   │   ├── events.py
│   │   │   ├── zones.py
│   │   │   ├── ppe.py
│   │   │   ├── tracks.py
│   │   │   ├── incidents.py
│   │   │   ├── evidence.py
│   │   │   ├── analytics.py
│   │   │   ├── system_health.py
│   │   │   └── models.py
│   │   ├── services/           # Business logic
│   │   │   ├── __init__.py
│   │   │   ├── camera_manager.py
│   │   │   ├── detection_pipeline.py
│   │   │   ├── tracker.py
│   │   │   ├── ppe_detector.py
│   │   │   ├── zone_engine.py
│   │   │   ├── proximity_engine.py
│   │   │   ├── fall_detector.py
│   │   │   ├── fire_smoke_detector.py
│   │   │   ├── leakage_detector.py
│   │   │   ├── event_engine.py
│   │   │   ├── evidence_engine.py
│   │   │   ├── alert_engine.py
│   │   │   ├── workflow_engine.py
│   │   │   └── health_monitor.py
│   │   ├── engine/             # Core engines
│   │   │   ├── __init__.py
│   │   │   ├── temporal_verifier.py
│   │   │   ├── rule_engine.py
│   │   │   └── state_machine.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── video_buffer.py
│   │       ├── geometry.py
│   │       ├── logger.py
│   │       └── metrics.py
│   ├── migrations/
│   │   └── versions/
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_zone_geometry.py
│   │   ├── test_temporal_verification.py
│   │   ├── test_event_workflow.py
│   │   └── test_not_assessable.py
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── css/
│   │   └── styles.css
│   ├── js/
│   │   ├── app.js
│   │   ├── dashboard.js
│   │   ├── cameras.js
│   │   ├── events.js
│   │   ├── evidence.js
│   │   ├── analytics.js
│   │   └── websocket.js
│   └── assets/
├── ai_models/
│   ├── __init__.py
│   ├── person_detector.py
│   ├── ppe_detector.py
│   ├── pose_estimator.py
│   ├── fire_smoke_detector.py
│   ├── leakage_detector.py
│   ├── model_registry.py
│   └── configs/
├── scripts/
│   ├── init_db.py
│   ├── seed_config.py
│   ├── run_tests.py
│   └── validate_models.py
├── docs/
│   ├── API.md
│   ├── MODEL_EVALUATION.md
│   ├── VALIDATION_STATUS.md
│   ├── SECURITY.md
│   └── DEPLOYMENT.md
└── data/
    ├── database.db
    ├── evidence/
    └── logs/
```