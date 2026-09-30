# API Surface

All application routes are under `/api/v1`. Authentication uses a bearer JWT.
Every response carries an `X-Request-ID` header; a caller-supplied value is
echoed only when it is a short, restricted character set, otherwise a new
identifier is generated.

## Implemented Routes

- `POST /auth/login`, `GET /auth/me`, user administration, and audit-log listing. User and audit listing are paginated with bounded `limit`/`offset`.
- `GET/POST /cameras`, `GET/PUT/DELETE /cameras/{camera_id}`, and `GET /cameras/{camera_id}/health`. Camera listing is paginated.
- `GET /events` reads persisted events; `POST /events/{event_id}/transitions` applies only allowed workflow transitions and records actor/reason history.
- `GET /events/{event_id}/evidence` lists evidence linked to a persisted event; `GET /events/{event_id}/evidence/{evidence_id}/content` serves an existing in-root file only.
- `GET /notifications` lists the authenticated user's notifications; `POST /notifications/events/{event_id}` queues an in-app item or returns external channels as `NOT_CONFIGURED`.
- `GET/POST /configuration/plants`, `/configuration/areas`, `/configuration/zones`, and `/configuration/ppe-rules` provide authenticated operator-supplied configuration. Writes are audited. Configured PPE thresholds require a source reference and remain `NOT_VALIDATED`.
- `GET /analytics/summary` returns counts from database records and `NOT_MEASURED` accuracy state.
- `GET /system/health`, `GET /system/ai-health`, and `GET /system/pipelines` expose actual subsystem, model, and pipeline state.
- `GET/POST /events/{event_id}/acknowledgements` records an operator acknowledgement and advances the event in one transaction. Repeating it returns `409`.
- `GET/POST /events/{event_id}/assignments` assigns an acknowledged event to an active user. Assigning an inactive user, or an event that is not `ACKNOWLEDGED`, returns `409`.
- `GET/POST /incidents`, `GET /incidents/{incident_id}`, and `POST /incidents/{incident_id}/transitions` manage the incident lifecycle; `GET /incidents/{incident_id}/transitions` returns its history.
- `GET/POST /near-misses` and `POST /near-misses/{near_miss_id}/transitions` manage the near-miss lifecycle; `GET /near-misses/{near_miss_id}/transitions` returns its history.
- `GET/POST /corrective-actions` and `POST /corrective-actions/{action_id}/transitions` manage corrective actions; `GET /corrective-actions/{action_id}/transitions` returns its history. A corrective action must name exactly one existing parent (`event_id`, `incident_id`, or `near_miss_id`).
- `GET /lifecycle-states` publishes the declared state machine for every response entity, so a client never has to infer allowed states.

Response writes require `ADMIN` or `SAFETY_OFFICER`. Every transition requires a
reason, is validated against the declared state map, and is recorded with its
actor and previous state. An illegal transition returns `409` and changes
nothing.

No response endpoint creates an `Event`. Incidents, near-misses, and corrective
actions are raised by an authenticated operator against an event that already
exists, because no safety detector is connected to this platform.

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

There is no event-generation API, PPE/zone/proximity/fall/fire/smoke detector
API, WebSocket, live video stream, or user-administration UI. The system does
not create an event from a detection automatically. Empty analytics reflect DB
counts only.