# Project Structure

This map describes the active root project. `.kilo/worktrees/thin-credit` is a separate worktree and is not part of this application's source.

Status vocabulary:

- `IMPLEMENTED`: code path exists and is covered by software tests.
- `PARTIAL`: code path exists but has external dependencies or incomplete operational behavior.
- `NOT_CONFIGURED`: required operator-supplied configuration is absent.
- `NOT_VALIDATED`: no authorized representative evaluation has been completed.
- `BLOCKED`: implementation or validation cannot proceed without an external dependency.

```text
.
|-- ai_models/
|   |-- base_model.py
|   |-- detection.py
|   `-- ultralytics_adapter.py        # Explicit local weights; no downloads
|-- backend/
|   |-- alembic.ini
|   |-- requirements.txt
|   |-- app/
|   |   |-- access_control.py        # No authentication; fail-closed switch
|   |   |-- config.py                # Environment settings and non-local guard
|   |   |-- database.py              # Migration-owned schema and head checks
|   |   |-- main.py                  # FastAPI lifespan and workers
|   |   |-- models.py                # SQLAlchemy domain models
|   |   |-- api/
|   |   |   |-- cameras.py           # CRUD, webcam discovery/control, MJPEG/snapshot
|   |   |   |-- events.py             # Persisted events and workflow transitions
|   |   |   |-- responses.py          # Acknowledgement, assignment, response lifecycles
|   |   |   |-- identity.py           # Operator directory and audit log; no login
|   |   |   |-- safety_config.py      # Detector, thresholds, escalation/notification policies
|   |   |   |-- escalation.py         # Escalation and correlation routes
|   |   |   |-- evidence.py           # Event-linked evidence retrieval
|   |   |   |-- notifications.py      # Queue and channel status
|   |   |   |-- analytics.py          # Persisted-record count analytics
|   |   |   |-- configuration.py      # Plant/area/zone/PPE configuration
|   |   |   `-- system_health.py      # Health, model, detector, pipeline state
|   |   |-- engine/temporal_verifier.py
|   |   |-- services/
|   |   |   |-- webcam_source.py      # Real webcam probe/open helpers
|   |   |   |-- video_ingestion.py    # Shared webcam/RTSP/file frame reader
|   |   |   |-- inference_pipeline.py # Frame-to-model-to-track pipeline
|   |   |   |-- safety_engine.py      # Supported detector capability/evaluation
|   |   |   |-- safety_orchestrator.py# Detection, event, evidence, correlation junction
|   |   |   |-- continuous_health.py  # Camera health polling
|   |   |   |-- health_monitor.py     # Observed health and CAMERA_FAILURE events
|   |   |   |-- evidence_engine.py    # Snapshot/hash/path/integrity handling
|   |   |   |-- notification_engine.py# Queue bookkeeping; no delivery worker
|   |   |   |-- escalation_engine.py  # Persisted-event escalation policy evaluation
|   |   |   |-- correlation_engine.py # Correlation of persisted events
|   |   |   |-- workflow_engine.py    # Event acknowledgement/assignment/transitions
|   |   |   |-- response_engine.py    # Incident/near-miss/action lifecycles
|   |   |   |-- zone_engine.py        # Normalized polygon validation/membership
|   |   |   `-- tracker.py            # Visual-only IoU tracking
|   |   |-- utils/
|   |   |   |-- frame_buffer.py       # Bounded timestamped frame buffer
|   |   |   |-- encrypted_url.py      # Camera URL encryption policy
|   |   |   `-- redaction.py          # Shared source credential redaction
|   |-- migrations/versions/          # 12 Alembic revisions; linear chain
|   `-- tests/                        # Isolated DB, API, safety, migration tests
|-- config/igl/                       # Empty NOT_CONFIGURED templates; not auto-loaded
|-- data/                             # Git-ignored runtime DB, logs, evidence, reports
|-- docs/                             # Setup, architecture, API, operations, validation, security
|-- frontend/
|   |-- index.html
|   |-- css/styles.css
|   `-- js/app.js                     # Dashboard, webcam preview, event alarm, config forms
|-- scripts/
|   |-- run_inference.py              # Model-backed validation runner
|   `-- test_webcam.py                # Real device discovery/capture measurement
|-- .env.example                      # Local-only settings template; no secrets
`-- README.md
```

## Current Boundaries

- Webcam discovery, start/stop, frame capture, and browser MJPEG preview were exercised on this laptop. This does not validate AI inference.
- Fire/smoke presence and restricted-zone evaluation are connected only when compatible real weights, classes, and operator configuration are present.
- PPE absence, proximity, fall, leakage, and unsafe-behavior detectors are `NOT_IMPLEMENTED`.
- Authentication and external notification delivery are `NOT_IMPLEMENTED`; local development must remain on localhost.
- IGL site data, SOPs, model weights, and labeled validation data are `NOT_CONFIGURED` / `NOT_VALIDATED`.