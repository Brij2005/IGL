# API Surface

All application routes are under `/api/v1`. Authentication uses a bearer JWT.

## Implemented Routes

- `POST /auth/login`, `GET /auth/me`, user administration, and audit-log listing.
- `GET/POST /cameras`, `GET/PUT/DELETE /cameras/{camera_id}`, and `GET /cameras/{camera_id}/health`.
- `GET /events` reads persisted events; `POST /events/{event_id}/transitions` applies only allowed workflow transitions and records actor/reason history.
- `GET /events/{event_id}/evidence` lists evidence linked to a persisted event; `GET /events/{event_id}/evidence/{evidence_id}/content` serves an existing in-root file only.
- `GET /notifications` lists the authenticated user's notifications; `POST /notifications/events/{event_id}` queues an in-app item or returns external channels as `NOT_CONFIGURED`.
- `GET /analytics/summary` returns counts from database records and `NOT_MEASURED` accuracy state.
- `GET /system/ai-health`, `GET /system/pipelines`, and `GET /system/health` expose actual model/pipeline/system state.

`GET /` is a derived health summary, not evidence of real input or AI validation.

## Not Implemented

There is no event-generation API, PPE/zone/proximity/fall/fire/smoke detector API, event detail workflow UI, WebSocket, or frontend. The system does not create an event from a detection automatically. Empty analytics reflect DB counts only.