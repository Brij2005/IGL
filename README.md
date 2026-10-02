# IGL Industrial AI Safety Platform

## Current Status

- **IMPLEMENTED:** Migration-owned FastAPI/SQLAlchemy backend, real webcam discovery and dashboard preview through the shared capture buffer, RTSP/file ingestion, explicit-path Ultralytics adapter, detection validation, visual-only tracking, configured fire/smoke/restricted-zone event orchestration, temporal verification, event-linked evidence/correlation, audited response lifecycles, and truthful health states.
- **PARTIALLY IMPLEMENTED:** Safety event evaluation requires a compatible model and authorized detector/zone configuration. PPE absence, proximity, fall, leakage, and unsafe-behavior detection are not implemented. SMTP email and Meta WhatsApp Cloud API transports now have real queued delivery and operator test-send endpoints; webhook/SMS/Teams/physical buzzer transports remain unavailable.
- **WEBCAM CHECK (CURRENT WINDOWS RUN):** OpenCV 4.14.0.94 could not open device indices 0–5 through DirectShow, Media Foundation (MSMF), or automatic backend selection. Windows PnP also returned no present Camera devices. Device 0 returned no frames, preview is unavailable, and measured FPS is `null`. A webcam preview reported by a prior run was not reproduced. Enable/connect the camera and Windows desktop-app camera access, then rerun the documented probe.
- **BROWSER CHECK:** Microsoft Edge rendered the dashboard through the local static server. On a separate temporary migrated database, sign-in, JWT identity, Cameras/Events/Configuration navigation, password change and old-token rejection, re-login, logout, and a 390px layout were exercised. No operational event was created for validation.
- **NOTIFICATIONS / ALARM:** Real API test sends returned `EMAIL_NOT_CONFIGURED` and `WHATSAPP_NOT_CONFIGURED`. The existing database had no unacknowledged event among the 100 records returned, so audible alarm acknowledgement/escalation was not validated against a live unacknowledged event. Software lifecycle behavior is covered by backend tests; no physical alarm is connected or claimed.
- **NOT_CONFIGURED:** No model weights are present. The default model state is `MODEL_NOT_CONFIGURED`; no detections are fabricated and weights are never downloaded automatically.
- **IMPLEMENTED WITH DEPLOYMENT LIMITS:** Password login, bcrypt password storage, signed short-lived JWT bearer tokens, role/permission checks, login throttling, one-time initial-admin provisioning, and self-service password changes. Development still defaults to anonymous mode for localhost only; production/staging require anonymous access disabled and a JWT signing secret.
- **NOT_VALIDATED:** No IGL validation data or metrics are available. This repository does not claim production readiness for any phase.
- **BLOCKED_BY_EXTERNAL_INPUT:** Real inference/detection/alarm validation requires an authorized compatible checkpoint and source/configuration. IGL validation additionally requires approved IGL footage, layout/SOPs, and labeled evaluation data.

No authorized IGL camera footage, plant layout, PPE SOP dataset, model checkpoint, or labeled IGL validation dataset is currently present in this workspace. No substitute data has been created to fill these gaps.

## Local Setup

Use Python 3.13 or another environment compatible with the pinned packages.

```powershell
python -m pip install -r backend/requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
python -m alembic -c backend/alembic.ini upgrade head
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

The example config uses anonymous development access. Keep it bound to localhost; do not expose it to an untrusted network. For shared deployment, disable anonymous access and configure JWT signing and initial-admin settings as described in [docs/setup.md](docs/setup.md).

In a second PowerShell terminal, start the static frontend with `python -m http.server 8001 --directory frontend`, then open `http://127.0.0.1:8001`.

The default development database is `data/database.db`. It is git-ignored. Never delete or recreate an existing database to apply this setup. See [docs/setup.md](docs/setup.md) for configuration and production safeguards.

## Health Semantics

`GET /` and `GET /api/v1/system/health` report observation rather than assumption. `APPLICATION_UP` states only that the process is serving; it is separate from `DATABASE_OK`/`DATABASE_UNAVAILABLE`, `MIGRATIONS_CURRENT`/`MIGRATIONS_PENDING_OR_UNAVAILABLE`, `MODEL_CONFIGURED`/`MODEL_NOT_CONFIGURED`, and `NO_CAMERA`/`CAMERA_CONFIGURED_NOT_OBSERVED`/`CAMERA_AVAILABLE`. A camera is `CAMERA_AVAILABLE` only when its active running stream has delivered a real frame; an active record alone is `CAMERA_CONFIGURED_NOT_OBSERVED`. A pipeline is `RUNNING` only after a completed inference. `measured_performance` is `NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`: no FPS, latency, or accuracy figure is reported unless it was measured from a real observed frame. Every response carries an `X-Request-ID` correlation header.

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

The platform is advisory only and never controls PLCs, machinery, valves, interlocks, or emergency systems. Camera failures are automatically recorded as `CAMERA_FAILURE` with `NOT_ASSESSABLE`; supported safety events can only come from real model detections through configured detectors and zones. The browser alarm reacts only to persisted, confirmed safety events and can persist an acknowledgement; it does not create events. See [docs/architecture.md](docs/architecture.md), [docs/api.md](docs/api.md), and [docs/ai_validation.md](docs/ai_validation.md).

The frontend is a partial operational dashboard with webcam preview when a device is available, system health, event review, evidence, analytics, configuration, audit, and a persisted-event local alarm. It includes a JWT sign-in screen and SMTP/WhatsApp test sends; full user administration and physical alarm control are not implemented. See [docs/igl_configuration.md](docs/igl_configuration.md).
