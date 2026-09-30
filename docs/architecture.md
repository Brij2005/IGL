# Architecture

## Phase 4 Data Path

`Camera configuration -> StreamReader -> bounded FrameBuffer -> UltralyticsModelAdapter -> Detection -> IoUTracker`

`StreamReader` accepts a configured RTSP/HTTP source or local video path. Frames are timestamped and copied into a thread-safe buffer bounded by retention time, frame count, and bytes. The adapter loads only an explicitly configured local weights file. Missing or invalid weights produce a visible model status and zero detections. Inference access is serialized across camera workers.

Each `Detection` validates camera, timestamp, class, confidence, bounding box, and model metadata. Each `VisualTrack` has a generated visual-session identifier and a `DETECTED`, `TRACKED`, `TEMPORARILY_LOST`, or `ENDED` lifecycle. Track identifiers are not employee identities; identity is not inferred or attached.

The authenticated `/api/v1/system/health` and `/api/v1/system/ai-health` endpoints expose derived subsystem states. `/api/v1/system/pipelines` exposes per-camera pipeline status. `/api/v1/events` reads persisted event records; `/api/v1/events/{event_id}/transitions` enforces allowed workflow transitions and records transition history. Evidence capture and notification queuing are available only for persisted events and are not connected to detection because no safety event-generation engine is implemented. No PPE model, proximity, fall, or fire detector is connected.

Standalone `TemporalVerifier`, polygon membership, and PPE rule interpretation primitives are present. They require explicitly supplied rules/observations and do not generate events. Generic temporal values must be marked `ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION`; no IGL SOP thresholds or zone geometry are present.

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

- **IMPLEMENTED:** Bounded frame buffering, local model adapter, detection structure, IoU track association, camera-to-pipeline wiring, continuous camera-health worker, auditable event state transition service, one-time administrator bootstrap, migration-owned schema, and derived health semantics.
- **PARTIALLY IMPLEMENTED:** Temporal verification, zone geometry assessment, and PPE rule evaluation as independent non-event-generating primitives.
- **PARTIALLY IMPLEMENTED:** Operator-supplied configuration APIs/templates, event-linked evidence storage, in-app notification queue, DB count analytics, and HTML/CSS/JS views. The UI has no live video feed and is not a complete operations console.
- **PARTIALLY IMPLEMENTED:** File and RTSP acquisition code. No actual video source was available for end-to-end verification.
- **NOT_CONFIGURED:** Model weights are absent; model health reports `MODEL_NOT_CONFIGURED`.
- **NOT_VALIDATED:** No IGL layouts, cameras, SOPs, or labeled IGL data were supplied or assessed.
- **BLOCKED_BY_REAL_INPUT:** Detection accuracy, tracking accuracy, sustained real-time performance, and IGL validation all require authorized real input that is absent from this workspace.

The system is advisory only. No safety instrumented or operational control systems are called.