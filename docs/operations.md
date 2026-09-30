# Operations Runbook

## Startup

1. Configure secrets and DB URL in the deployment environment.
2. Run `alembic -c backend/alembic.ini upgrade head`.
3. For a first empty deployment, run `python scripts/bootstrap_admin.py` interactively once.
4. Start `uvicorn backend.app.main:app`.

The API refuses startup if the DB revision is not at the Alembic head. Startup does not create schema. The camera health worker starts with the app and stops before pipeline shutdown.

## Current States

- No model weights: `MODEL_NOT_CONFIGURED`.
- No camera config/source: no pipeline exists; camera metrics remain absent.
- Camera with no observed frame: `CONFIGURED` or `CONNECTING`, with measured values `null`.
- Source unavailable or timed out: offline/timeout state, not healthy monitoring.
- No external notification sender: external notification records remain `NOT_CONFIGURED`.
- No verified real input: validation remains `NOT_VALIDATED`.

Run `python scripts/run_inference.py --help` for the actual input-validation command. Reports and evidence are sensitive operational data; restrict filesystem and report access.

## Recovery and Limitations

Use additive Alembic migrations; do not delete/recreate databases. Back up DB and evidence before planned migration/key rotation. Current health monitoring is polling-based and process-local; camera operation and deployment behavior need real authorized source testing. No IGL layout, SOP, or model-validation data is included.