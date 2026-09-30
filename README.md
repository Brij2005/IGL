# IGL Industrial AI Safety Platform

## Current Status

- **IMPLEMENTED:** FastAPI authentication/camera/event/evidence/notification/analytics/configuration APIs, SQLAlchemy domain models behind a migration-owned schema, bounded rolling frame buffer, explicit-path Ultralytics adapter, detection validation, visual-only IoU tracking, continuous observed camera-health polling, audited event workflow transitions, one-time operator-supplied admin bootstrap, derived health semantics, and credential redaction shared by APIs and reports.
- **PARTIALLY IMPLEMENTED:** RTSP and prerecorded-file ingestion are connected to the Phase 4 pipeline, but no real source has been tested in this workspace.
- **NOT_CONFIGURED:** No model weights are present. The default model state is `MODEL_NOT_CONFIGURED`; no detections are fabricated and weights are never downloaded automatically.
- **PARTIALLY IMPLEMENTED:** Temporal verification, polygon membership, and PPE-rule interpretation are standalone software primitives; they are not connected to a real safety detector or event generation.
- **NOT_VALIDATED:** No IGL validation data or metrics are available. This repository does not claim production readiness for any phase.
- **BLOCKED_BY_REAL_INPUT:** Detection accuracy, tracking accuracy, sustained real-time performance, and IGL validation cannot be assessed without authorized footage, weights, and labeled data.

No authorized IGL camera footage, plant layout, PPE SOP dataset, model checkpoint, or labeled IGL validation dataset is currently present in this workspace. No substitute data has been created to fill these gaps.

## Local Setup

Use Python 3.13 or another environment compatible with the pinned packages.

```powershell
python -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
alembic -c backend/alembic.ini upgrade head
python scripts/bootstrap_admin.py
uvicorn backend.app.main:app --reload
```

The bootstrap command prompts for a new username, email, full name, and password of at least 12 characters. It refuses to run after any user exists, never uses a default password, and startup never creates an administrator. A non-interactive `--configured` mode requires `BOOTSTRAP_ADMIN_ENABLED=true` plus every `BOOTSTRAP_ADMIN_*` value.

The default development database is `data/database.db`. It is git-ignored. Never delete or recreate an existing database to apply this setup. See [docs/setup.md](docs/setup.md) for configuration and production safeguards.

## Health Semantics

`GET /` and `GET /api/v1/system/health` report observation rather than assumption. `APPLICATION_UP` states only that the process is serving; it is separate from `DATABASE_OK`/`DATABASE_UNAVAILABLE`, `MIGRATIONS_CURRENT`/`MIGRATIONS_PENDING_OR_UNAVAILABLE`, `MODEL_CONFIGURED`/`MODEL_NOT_CONFIGURED`, and `NO_CAMERA`/`CAMERA_AVAILABLE`. A pipeline is `RUNNING` only after a completed inference. `measured_performance` is `NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`: no FPS, latency, or accuracy figure is reported unless it was measured from a real observed frame. Every response carries an `X-Request-ID` correlation header.

## Tests

```powershell
python -m pytest backend/tests -q
```

The suite migrates a temporary database with the real Alembic chain and never touches your configured database. See [docs/setup.md](docs/setup.md).

## Run Real-Input Validation

Provide one authorized source (`VIDEO_SOURCE` for a local video or `RTSP_URL` for an IP stream) and a readable local `.pt` checkpoint with explicit `MODEL_NAME` and `MODEL_VERSION` in `.env`. Set `MODEL_CONFIDENCE_THRESHOLD` for that model. Set `CAMERA_URL_ENCRYPTION_KEY` before adding credential-bearing camera URLs. Alternatively, pass a local file or credential-free URL with `--source`.

```powershell
python scripts/run_inference.py --source "C:\authorized\path\sample.mp4"
python scripts/run_inference.py --duration-seconds 30
```

The second command reads `RTSP_URL` from the environment without echoing it. A JSON report is written under `data/validation_reports/`, which is git-ignored. Reports record `input_provenance`; the CLI always reports `AUTHORIZED_REAL_INPUT`, and a report claiming `REAL_INPUT_VALIDATED` is rejected unless real frames and a completed inference were observed with no errors. The test suite runs with `TEST_FIXTURE` provenance and is recorded as `TEST_FIXTURE_EXECUTION`, never as validation. No model weights are downloaded. Missing or invalid inputs fail with explicit status codes. See [docs/validation.md](docs/validation.md).

## Scope

The platform is an advisory layer. It does not control PLCs, machinery, valves, interlocks, or emergency systems. There is no safety event-generation engine, no PPE/proximity/fall/fire/leakage detector, no live video stream, and no face recognition or identity resolution. See [docs/architecture.md](docs/architecture.md), [docs/api.md](docs/api.md), and [docs/ai_validation.md](docs/ai_validation.md).

The frontend is a partial operational dashboard for the existing login, camera, event, evidence, analytics, configuration, audit, and system-health APIs. It does not provide live video or complete user administration. See [docs/igl_configuration.md](docs/igl_configuration.md).