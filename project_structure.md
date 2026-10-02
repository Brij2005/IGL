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
|   |   |-- access_control.py        # JWT identity resolution/RBAC; dev anonymous switch
|   |   |-- config.py                # Environment settings and non-local guard
|   |   |-- database.py              # Migration-owned schema and head checks
|   |   |-- main.py                  # FastAPI lifespan and workers
|   |   |-- models.py                # SQLAlchemy domain models
|   |   |-- api/
|   |   |   |-- auth.py               # Password login, bearer tokens, password change
|   |   |   |-- cameras.py           # CRUD, webcam discovery/control, MJPEG/snapshot
|   |   |   |-- events.py             # Persisted events and workflow transitions
|   |   |   |-- responses.py          # Acknowledgement, assignment, response lifecycles
|   |   |   |-- identity.py           # Operator account directory and audit log
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
|   |   |   |-- notification_engine.py# Durable queue, deduplication, retries, status
|   |   |   |-- notification_delivery.py # SMTP + Meta WhatsApp Cloud API transports
|   |   |   |-- notification_worker.py # Background delivery and retry worker
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
|   |-- migrations/versions/          # 14 Alembic revisions; linear chain
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

- A prior local record reports webcam preview. The current execution's device probe did not open or enumerate a webcam, so that result was not reproduced; neither observation validates AI inference.
- Fire/smoke presence and restricted-zone evaluation are connected only when compatible real weights, classes, and operator configuration are present.
- PPE absence, proximity, fall, leakage, and unsafe-behavior detectors are `NOT_IMPLEMENTED`.
- JWT authentication and SMTP/WhatsApp Cloud API delivery are implemented. Anonymous local development must remain on localhost; physical alarm output and other external notification channels are not implemented.
- IGL site data, SOPs, model weights, and labeled validation data are `NOT_CONFIGURED` / `NOT_VALIDATED`.
