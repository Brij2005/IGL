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
|   `-- ultralytics_adapter.py        # Explicit local weights; .pt/.onnx/.engine; no downloads
|-- backend/
|   |-- alembic.ini
|   |-- requirements.txt
|   |-- app/
|   |   |-- access_control.py        # JWT identity resolution/RBAC; dev anonymous switch
|   |   |-- config.py                # Environment settings, validators, non-local guard
|   |   |-- database.py              # Migration-owned schema and head checks
|   |   |-- main.py                  # FastAPI lifespan, workers, root health summary
|   |   |-- models.py                # SQLAlchemy domain models
|   |   |-- api/
|   |   |   |-- auth.py               # Password login, bearer tokens, password change
|   |   |   |-- cameras.py            # CRUD, webcam probe/register/start/stop/status, MJPEG, snapshot
|   |   |   |-- events.py             # Persisted events, incident_ids linkage, workflow transitions
|   |   |   |-- responses.py          # Acknowledgement, assignment, response lifecycles
|   |   |   |-- identity.py           # Operator account directory and audit log
|   |   |   |-- safety_config.py      # Detector, thresholds, escalation/notification policies
|   |   |   |-- escalation.py         # Escalation and correlation routes
|   |   |   |-- evidence.py           # Event-linked evidence retrieval
|   |   |   |-- notifications.py      # Queue, channel status, EMAIL/WHATSAPP test sends
|   |   |   |-- analytics.py          # Persisted-record count analytics
|   |   |   |-- configuration.py      # Plant/area/zone/PPE configuration
|   |   |   |-- alarms.py             # Alarm centre routes, policy, physical status/test
|   |   |   `-- system_health.py      # Health, model, detector, pipeline state
|   |   |-- engine/temporal_verifier.py
|   |   |-- services/
|   |   |   |-- webcam_source.py      # Real webcam probe/open, preferred capture backend
|   |   |   |-- video_ingestion.py    # Shared webcam/RTSP/file reader; REOPENING/REOPENED states
|   |   |   |-- inference_pipeline.py # Frame-to-model-to-track; operator-stop marker persistence
|   |   |   |-- safety_engine.py      # Detector catalogue, capability reporting, rule parameters
|   |   |   |-- safety_orchestrator.py# Detection, event, evidence, correlation, alarm junction
|   |   |   |-- continuous_health.py  # Camera health polling; never restarts an operator-stopped camera
|   |   |   |-- health_monitor.py     # Observed health and CAMERA_FAILURE events
|   |   |   |-- evidence_engine.py    # Snapshot/hash/path/integrity handling
|   |   |   |-- alarm_engine.py       # Policy evaluation, raise/suppress, acknowledge, escalate, clear, expire
|   |   |   |-- physical_alarm.py     # HTTP/MQTT/serial/GPIO actuator transports; strict state vocabulary
|   |   |   |-- notification_engine.py# Durable queue, dedup, retries, delivery-status vocabulary
|   |   |   |-- notification_templates.py # Fixed placeholder set; NOT_SUBSTITUTED reporting
|   |   |   |-- notification_delivery.py # SMTP + Meta WhatsApp Cloud API transports
|   |   |   |-- notification_worker.py # Background delivery and retry worker
|   |   |   |-- escalation_engine.py  # Persisted-event escalation; moves linked alarms to ESCALATED
|   |   |   |-- correlation_engine.py # Correlation of persisted events
|   |   |   |-- workflow_engine.py    # Event acknowledgement/assignment/transitions
|   |   |   |-- response_engine.py    # Incident/near-miss/action lifecycles
|   |   |   |-- system_health.py      # Uptime, disk, transports, alarm and measured-performance aggregation
|   |   |   |-- zone_engine.py        # Normalized polygon validation/membership
|   |   |   `-- tracker.py            # Visual-only IoU tracking
|   |   |-- utils/
|   |   |   |-- frame_buffer.py       # Bounded timestamped frame buffer
|   |   |   |-- encrypted_url.py      # Camera URL encryption policy
|   |   |   `-- redaction.py          # Shared source credential redaction
|   |-- migrations/versions/          # 15 Alembic revisions; linear chain
|   `-- tests/                        # Isolated DB, API, safety, migration, alarm tests
|-- config/igl/                       # Empty NOT_CONFIGURED templates; not auto-loaded
|   |-- plant.yaml
|   |-- areas.yaml
|   |-- zones.yaml                    # + hazard_description, ppe_requirements, approval fields
|   |-- cameras.yaml
|   |-- equipment.yaml
|   |-- safety_rules.yaml
|   |-- ppe_rules.yaml
|   |-- escalation_rules.yaml         # + alarm_policy_reference block
|   |-- notification_recipients.yaml  # New; recipient schema + evidence_retention_reference block
|   `-- README.md
|-- data/                             # Git-ignored runtime DB, logs, evidence, reports
|-- docs/
|   |-- setup.md
|   |-- architecture.md
|   |-- api.md
|   |-- operations.md
|   |-- security.md
|   |-- deployment.md
|   |-- validation.md
|   |-- ai_validation.md
|   |-- troubleshooting.md
|   `-- igl_configuration.md
|-- frontend/
|   |-- index.html
|   |-- css/styles.css                # Wrapping status pills/banners; no horizontal overflow
|   `-- js/app.js                     # Dashboard, alarm centre, worker tracks, rules, notifications
|-- scripts/
|   |-- run_inference.py              # Model-backed real-input validation runner
|   |-- validate_model.py             # Model artifact validation without a video source
|   |-- test_webcam.py                # Real device discovery/capture measurement
|   |-- smoke_browser.mjs             # Edge CDP render/overflow/camera cycle check
|   |-- start_backend.ps1
|   |-- start_frontend.ps1
|   |-- test_frontend_smoke.ps1
|   `-- validate_system.ps1
|-- .env.example                      # Local-only settings template; no secrets
|-- project_structure.md
`-- README.md
```

## Notable New Files

- `backend/app/models.py`: `Alarm` and `AlarmStateTransition` (section "5b. ALARM SUBSYSTEM").
- `backend/migrations/versions/b2a5c8e4f701_015_alarm_subsystem.py`: creates `alarms` and `alarm_state_transitions` with their indexes. Revision `b2a5c8e4f701` follows `e7d2e6c3a991`.
- `backend/app/services/alarm_engine.py`, `backend/app/services/physical_alarm.py`, `backend/app/services/notification_templates.py`.
- `backend/app/api/alarms.py`, mounted at `/api/v1/alarms` from `backend/app/api/__init__.py`.
- `scripts/validate_model.py`.
- `config/igl/notification_recipients.yaml`.
- `docs/deployment.md`.

## Tests

`backend/tests/test_alarm_subsystem.py` holds 27 tests covering policy evaluation, cooldown and repeat-window suppression, state transitions with reasons, expiry and the physical state vocabulary. `backend/tests/test_model_validation.py` and `backend/tests/test_validation_runner.py` cover the model and real-input runners. `backend/tests/test_notification_templates.py` covers the placeholder contract. `backend/tests/test_camera_ingestion.py` covers the reopen states, reconnect state and the operator-stop marker. `backend/tests/test_health_semantics.py` covers the health vocabulary. The full suite is 365 tests, all passing on this machine.

## Current Boundaries

- The webcam result is real and reproducible on this machine: the registered "Laptop Webcam" opened through DSHOW, delivered 165 real frames, and reported `camera_fps=8.03`. That validates capture only. It does not validate AI inference, detection or tracking.
- `RESTRICTED_ZONE`, `FIRE`, `SMOKE`, `PERSON` and `PHONE` evaluation is code-connected but blocked at runtime by missing weights and IGL configuration.
- `HELMET`, `PPE`, `SAFETY_VEST`, `PROXIMITY`, `FALL`, `LEAKAGE` and `UNSAFE_BEHAVIOR` detectors are `NOT_IMPLEMENTED`.
- The alarm subsystem is implemented and tested. No live alarm has ever been raised on this machine because no confirmed event has ever existed here.
- Physical alarm output is implemented as four opt-in transports and reported `PHYSICAL_ALARM_NOT_CONFIGURED` until real hardware answers. Nothing is actuated and no hardware is claimed.
- JWT authentication and SMTP/WhatsApp Cloud API delivery are implemented but unconfigured here. Webhook, SMS, Teams and buzzer channels remain `NOT_IMPLEMENTED`.
- MFA, password recovery and centralised rate limiting are not implemented. Anonymous local development must remain on localhost.
- IGL site data, SOPs, model weights and labelled validation data are `NOT_CONFIGURED` / `NOT_VALIDATED`.
- `*.pt`, `*.onnx` and `*.engine` are git-ignored. No weights are distributed.
