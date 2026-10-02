# Architecture

## Phase 4 Data Path

`Camera/webcam/file/RTSP -> StreamReader -> bounded FrameBuffer -> UltralyticsModelAdapter -> Detection -> IoUTracker -> SafetyEventOrchestrator`

`StreamReader` accepts a configured webcam, RTSP/HTTP source, or local video path. Webcam discovery and preview reuse the same reader and bounded frame buffer; the browser preview is emitted only from observed frames. Frames are timestamped and copied into a thread-safe buffer bounded by retention time, frame count, and bytes. The adapter loads only an explicitly configured local weights file. Missing or invalid weights leave inference unavailable; no detections are substituted. Inference access is serialized across camera workers.

Each `Detection` validates camera, timestamp, class, confidence, bounding box, and model metadata. Each `VisualTrack` has a generated visual-session identifier and a `DETECTED`, `TRACKED`, `TEMPORARILY_LOST`, or `ENDED` lifecycle. Track identifiers are not employee identities; identity is not inferred or attached.

`/api/v1/system/health`, `/api/v1/system/ai-health`, and `/api/v1/system/pipelines` expose derived subsystem states. `/api/v1/events` reads persisted event records; workflow/acknowledgement APIs validate transitions and record history. The inference pipeline calls the safety orchestrator only after real model inference. Fire/smoke class presence and restricted-zone assessment are implemented conditionally on compatible classes, configured detector policy, and authorized zone geometry. PPE absence, proximity, fall, leakage, and unsafe-behavior detectors are not implemented. No such safety inference is currently available because model weights are absent.

## Automated Event Source

Camera-health monitoring can write `CAMERA_FAILURE` events with `observation_state=NOT_ASSESSABLE`, no confidence, and no duration; these report source availability, not a safety hazard. When an implemented detector has real model output and operator-supplied configuration, `SafetyEventOrchestrator` applies temporal policy, persists event provenance, captures evidence from the same frame, correlates persisted events, and evaluates notification/escalation policy. No safety event is produced by configuration alone.

Detections and visual tracks are persisted by the orchestrator with model and frame provenance when real inference runs. Incidents, near-misses, and corrective actions refer to persisted events and move only through declared, audited state machines. This build has no authentication, so actor attribution is explicitly NULL.

Temporal verification and polygon membership are connected to the supported safety evaluation path. PPE rule interpretation remains a separate primitive because PPE detectors are not implemented. Generic temporal values must be marked `ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION`; no IGL SOP thresholds or zone geometry are present.

## Health Semantics

Health output reports observation, never assumption. The application process being
up is reported as `APPLICATION_UP` and is separate from `DATABASE_OK` /
`DATABASE_UNAVAILABLE`, `MIGRATIONS_CURRENT` /
`MIGRATIONS_PENDING_OR_UNAVAILABLE`, `MODEL_CONFIGURED` /
`MODEL_NOT_CONFIGURED`, and `NO_CAMERA` / `CAMERA_AVAILABLE` /
`CAMERA_RECORDED_BUT_NONE_ACTIVE`. A pipeline is reported as `RUNNING` only
when at least one inference actually completed. `measured_performance` is
`NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`; no FPS, latency, or accuracy figure is
synthesised. Camera health rows store `NULL` rather than `0.0` for telemetry
that has not been measured, so an unobserved camera cannot present a
plausible-looking number. Migration `9c4b2e7a1d55` performs that change.

## Schema Ownership

The database schema is owned by the Alembic migration chain. No migration calls
`Base.metadata.create_all()` or `drop_all()`, and `init_db()` raises rather
than creating schema at runtime. Application startup compares the configured
database revision against the script heads and refuses to serve when they
differ. `backend/tests/conftest.py` migrates a temporary database so the test
suite never depends on a deployment's data.

## Configuration and Validation Boundaries

- **IMPLEMENTED:** Bounded frame buffering, local model adapter, detection structure, IoU track association and persistence, camera-to-pipeline wiring, continuous camera-health worker, audited event transitions, response lifecycles, migration-owned schema, and derived health semantics. Authentication/bootstrap are not implemented.
- **PARTIALLY IMPLEMENTED:** Supported fire/smoke/restricted-zone event path is code-connected but blocked at runtime by missing weights and IGL configuration; other listed detector categories remain unavailable.
- **PARTIALLY IMPLEMENTED:** Operator-supplied configuration APIs, event-linked evidence, in-app queue, DB-count analytics, browser dashboard and event-driven local alarm. External sender delivery and authentication are absent.
- **WEBCAM CHECK:** A previous local run records backend preview from device 0 at 640x480. In the current execution, device discovery and capture failed for device 0 and all probed indices, so that hardware result is historical and not reproduced here.
- **NOT_CONFIGURED:** Model weights are absent; model health reports `MODEL_NOT_CONFIGURED`.
- **NOT_VALIDATED:** No IGL layouts, cameras, SOPs, or labeled IGL data were supplied or assessed.
- **BLOCKED_BY_REAL_INPUT:** Model inference and detector validation require an authorized compatible model checkpoint. IGL validation additionally requires authorized plant layout/SOP data and labeled IGL footage.

The system is advisory only. No safety instrumented or operational control systems are called.
