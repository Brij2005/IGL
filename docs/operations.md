# Operations Runbook

## Startup

1. Configure secrets and DB URL in the deployment environment.
2. Run `alembic -c backend/alembic.ini upgrade head`.
3. For a first empty deployment, bootstrap the administrator once, interactively or with `python scripts/bootstrap_admin.py --configured` plus complete `BOOTSTRAP_ADMIN_*` values.
4. Start `uvicorn backend.app.main:app`.

The API refuses startup if the DB revision is not at the Alembic head. Startup does not create schema and does not create an administrator. The camera health worker starts with the app and stops before pipeline shutdown.

## Reported States

- The process is up: `APPLICATION_UP`. This is not health.
- No model weights: `MODEL_NOT_CONFIGURED`.
- No camera records: `NO_CAMERA`; cameras recorded but none active: `CAMERA_RECORDED_BUT_NONE_ACTIVE`.
- Camera with no observed frame: `CONFIGURED` or `CONNECTING`, with every measured value `null`.
- Source unavailable or timed out: offline/timeout state, not healthy monitoring.
- `overall_status` is `DEGRADED` with an explicit `degraded_reasons` list whenever the database, migrations, model, cameras, or the camera-health worker are not in a usable state.
- No external notification sender: external notification records remain `NOT_CONFIGURED`.
- No verified real input: `validation_status` is `NOT_VALIDATED` and `igl_validated` is `false`.
- No observed frame: `measured_performance` is `NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`.

Run `python scripts/run_inference.py --help` for the actual input-validation command. Reports and evidence are sensitive operational data; restrict filesystem and report access.

## Recovery and Limitations

Use additive Alembic migrations; do not delete/recreate databases. Back up the database and evidence before any planned migration or key rotation. Revision `9c4b2e7a1d55` rebuilds `camera_health` to make `fps` and `latency_ms` nullable and backfills placeholder zeros to `NULL`; it preserves all rows and was tested for upgrade, downgrade, and re-upgrade on a temporary database. Apply it with `alembic -c backend/alembic.ini upgrade head` after taking a backup.

Current health monitoring is polling-based and process-local; camera operation and deployment behavior need real authorized source testing. No IGL layout, SOP, or model-validation data is included.