# Operations Runbook

## Startup

1. Install `backend/requirements.txt` and copy `.env.example` to `.env` for the localhost demo.
2. From the project root, run `python -m alembic -c backend/alembic.ini upgrade head`.
3. Start the API with `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`.
4. Start the static dashboard with `python -m http.server 8001 --directory frontend`, then open `http://127.0.0.1:8001`.

The API refuses startup if the DB revision is not at the Alembic head. Startup does not create schema. When `INITIAL_ADMIN_*` is configured, it creates the first admin only if no admin exists. Development allows anonymous access only because `.env.example` opts into localhost demo mode. Production/staging require `ALLOW_ANONYMOUS_ACCESS=false`, `AUTH_JWT_SECRET_KEY`, and an existing active admin; configure the one-time bootstrap variables for first startup.

## Reported States

- The process is up: `APPLICATION_UP`. This is not health.
- No model weights: `MODEL_NOT_CONFIGURED`.
- No camera records: `NO_CAMERA`; cameras recorded but none active: `CAMERA_RECORDED_BUT_NONE_ACTIVE`.
- Camera with no observed frame: `CONFIGURED` or `CONNECTING`, with every measured value `null`.
- Source unavailable or timed out: offline/timeout state, not healthy monitoring.
- `overall_status` is `DEGRADED` with an explicit `degraded_reasons` list whenever the database, migrations, model, cameras, or the camera-health worker are not in a usable state.
- SMTP EMAIL and WhatsApp Cloud API channels are sent by the notification worker when configured. Other external channels remain `NOT_IMPLEMENTED`. A provider acceptance is recorded as `SENT`, but does not confirm the destination inbox or WhatsApp user received/read the message.
- No verified real input: `validation_status` is `NOT_VALIDATED` and `igl_validated` is `false`.
- No observed frame: `measured_performance` is `NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`.
- Observed camera failure: a `CAMERA_FAILURE` event is written with
  `observation_state=NOT_ASSESSABLE` and no confidence value, deduplicated per camera
  for 60 seconds. A camera configured with an unreachable source therefore produces
  real failure events; fix the source rather than suppressing the record.

Run `python scripts/run_inference.py --help` for the actual input-validation command. Reports and evidence are sensitive operational data; restrict filesystem and report access.

## Recovery and Limitations

Use additive Alembic migrations; do not delete/recreate databases. Back up the database and evidence before any planned migration or key rotation. Revision `9c4b2e7a1d55` rebuilds `camera_health` to make `fps` and `latency_ms` nullable and backfills placeholder zeros to `NULL`; it preserves all rows and was tested for upgrade, downgrade, and re-upgrade on a temporary database. Apply it with `alembic -c backend/alembic.ini upgrade head` after taking a backup.

Camera health polling, notification delivery, and active pipelines are process-local. A previous local dashboard run records a working laptop webcam; the current execution could not enumerate or open a webcam, so repeat `python scripts/test_webcam.py` on the intended operator machine. RTSP/file deployment behavior has not been validated here. No compatible model weights, IGL layout, SOP, or labeled model-validation data is included. SQLite is for local development; no production database or topology has been certified.
