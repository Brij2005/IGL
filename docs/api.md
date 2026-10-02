# API Surface

All application routes are under `/api/v1`. Authentication is not implemented;
the development default allows anonymous access. Declared permission/role
dependencies do not authorize a real identity in this build. Every response
carries a sanitized `X-Request-ID` header.

## Implemented Routes

- `GET/POST /identity/users`, identity role/state updates, and `GET /identity/audit-logs`. These are operator directory records, not authenticated accounts.
- `GET/POST /cameras`, `GET/PUT/DELETE /cameras/{camera_id}`, and camera status/health operations.
- `GET /cameras/webcam/devices`, `POST /cameras/webcam/register`, and camera source start/stop routes control the real webcam through the shared backend capture pipeline.
- `GET /cameras/{camera_id}/preview.mjpg` streams buffered real captures; `GET /cameras/{camera_id}/snapshot.jpg` returns only an observed real frame.
- `GET /events` reads persisted events; `POST /events/{event_id}/transitions` applies only allowed workflow transitions and records actor/reason history.
- `GET /events/{event_id}/evidence` lists evidence linked to a persisted event; `GET /events/{event_id}/evidence/{evidence_id}/content` serves an existing in-root file only.
- `GET /notifications` lists the queue; `POST /notifications/events/{event_id}` creates a queue record. No external delivery worker exists: absent transports report `NOT_CONFIGURED`; configured but unsupported transports report `NOT_IMPLEMENTED`, never `SENT`.
- `GET/POST /configuration/plants`, `/configuration/areas`, `/configuration/zones`, and `/configuration/ppe-rules` provide operator-supplied configuration. Detector/safety rules, thresholds, escalation policies, and notification policies are also under `/configuration/*`. Writes are audited with a NULL actor when anonymous. Validation claims cannot be submitted through safety-rule/threshold creation.
- `GET /analytics/summary` returns counts from database records and `NOT_MEASURED` accuracy state.
- `GET /system/health`, `GET /system/ai-health`, and `GET /system/pipelines` expose actual subsystem, model, and pipeline state.
- `GET/POST /events/{event_id}/acknowledgements` records an operator acknowledgement and advances the event in one transaction. Repeating it returns `409`.
- `GET/POST /events/{event_id}/assignments` assigns an acknowledged event to an active user. Assigning an inactive user, or an event that is not `ACKNOWLEDGED`, returns `409`.
- `GET/POST /incidents`, `GET /incidents/{incident_id}`, and `POST /incidents/{incident_id}/transitions` manage the incident lifecycle; `GET /incidents/{incident_id}/transitions` returns its history.
- `GET/POST /near-misses` and `POST /near-misses/{near_miss_id}/transitions` manage the near-miss lifecycle; `GET /near-misses/{near_miss_id}/transitions` returns its history.
- `GET/POST /corrective-actions` and `POST /corrective-actions/{action_id}/transitions` manage corrective actions; `GET /corrective-actions/{action_id}/transitions` returns its history. A corrective action must name exactly one existing parent (`event_id`, `incident_id`, or `near_miss_id`).
- `GET /lifecycle-states` publishes the declared state machine for every response entity, so a client never has to infer allowed states.

Response writes declare role/permission requirements, but no user is
authenticated in this build. With anonymous access enabled those requirements
are not identity authorization. Event acknowledgement persists a NULL actor;
state transitions are validated and recorded with previous/new states and a
reason. Illegal transitions return `409`.

Incidents, near-misses, and corrective actions refer to an existing event. The
inference pipeline can create supported detector events only when real model
weights, compatible classes, and detector/zone configuration are available.

`GET /` is a derived health summary, not evidence of real input or AI
validation. It reports `APPLICATION_UP` separately from `DATABASE_OK` /
`DATABASE_UNAVAILABLE`, `MIGRATIONS_CURRENT` /
`MIGRATIONS_PENDING_OR_UNAVAILABLE`, `MODEL_CONFIGURED` /
`MODEL_NOT_CONFIGURED`, and `NO_CAMERA` / `CAMERA_AVAILABLE` /
`CAMERA_RECORDED_BUT_NONE_ACTIVE`, plus `validation_status` and
`measured_performance`. The process being up is never reported as health.

## Response Safety

- No response model includes a password hash.
- Camera responses never include URL user information, query parameters, or
  fragments. An unparseable source is reduced to its final path segment.
- `detail` fields never carry a stream URL or credential.

## Not Implemented

There is no WebSocket, authentication API, external notification sender, or
complete user-administration UI. PPE absence, proximity, fall, leakage, and
unsafe-behavior detectors are not implemented. Fire/smoke presence and
restricted-zone evaluation are conditionally implemented, but cannot run until
a compatible model and authorized configuration are supplied. Camera failures
remain separate `NOT_ASSESSABLE` system observations.