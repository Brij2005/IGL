# IGL Industrial AI Safety Platform

## Current Status

- **IMPLEMENTED:** FastAPI authentication/camera APIs, SQLAlchemy domain models, bounded rolling frame buffer, explicit-path Ultralytics adapter, detection validation, visual-only IoU tracking, continuous observed camera-health polling, and audited event workflow transitions.
- **PARTIALLY IMPLEMENTED:** RTSP and prerecorded-file ingestion are connected to the Phase 4 pipeline, but no real source has been tested in this workspace.
- **NOT_CONFIGURED:** No model weights are present. The default model state is `MODEL_NOT_CONFIGURED`; no detections are fabricated and weights are never downloaded automatically.
- **PARTIALLY IMPLEMENTED:** Temporal verification, polygon membership, and PPE-rule interpretation are standalone software primitives; they are not connected to a real safety detector or event generation.
- **NOT_VALIDATED:** No IGL validation data or metrics are available. This repository does not claim production readiness for Phases 1–3.

No authorized IGL camera footage, plant layout, PPE SOP dataset, or labeled IGL validation dataset is currently present in this workspace.

## Local Setup

Use Python 3.13 or another environment compatible with the pinned packages.

```powershell
python -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
alembic -c backend/alembic.ini upgrade head
python scripts/bootstrap_admin.py
uvicorn backend.app.main:app --reload
```

The bootstrap command prompts for a new username, email, full name, and password. It refuses to run after any user exists and never uses a default password.

The default development database is `data/database.db`. Never delete or recreate an existing database to apply this setup. See [docs/setup.md](docs/setup.md) for configuration and production safeguards.

## Phase 4 Health

After authentication, inspect `GET /api/v1/system/ai-health` and `GET /api/v1/system/pipelines`. An offline source, unavailable model, or inference error is reported as a non-running/unavailable state; it is not treated as safe monitoring. Camera responses redact URL user information and credential-like query values.

The platform is an advisory layer. It does not control PLCs, machinery, valves, interlocks, or emergency systems. See [docs/architecture.md](docs/architecture.md) and [docs/ai_validation.md](docs/ai_validation.md).

## Run Real-Input Validation

Provide one authorized source (`VIDEO_SOURCE` for a local video or `RTSP_URL` for an IP stream) and a readable local `.pt` checkpoint with explicit `MODEL_NAME` and `MODEL_VERSION` in `.env`. Set `MODEL_CONFIDENCE_THRESHOLD` for that model. Set `CAMERA_URL_ENCRYPTION_KEY` before adding credential-bearing camera URLs. Alternatively, pass a local file or credential-free URL with `--source`.

```powershell
python scripts/run_inference.py --source "C:\authorized\path\sample.mp4"
python scripts/run_inference.py --duration-seconds 30
```

The second command reads `RTSP_URL` from the environment without echoing it. A JSON report is written under `data/validation_reports/`, which is git-ignored. No model weights are downloaded. Missing or invalid inputs fail with explicit status codes. See [docs/validation.md](docs/validation.md) for setup, metrics, and evidence requirements.

The frontend is not implemented yet. The current backend exposes truthful camera, event, evidence, notification, analytics, model, and system-health API surfaces; it does not provide PPE/event inference or IGL validation. See [docs/api.md](docs/api.md) and [docs/igl_configuration.md](docs/igl_configuration.md).