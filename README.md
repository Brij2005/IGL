# IGL Industrial AI Safety Platform

## Current Status

Status vocabulary used throughout this repository: `IMPLEMENTED` (code path exists and is covered by software tests), `TESTED` (verified by the automated suite on this machine), `REAL-WORLD VALIDATED` (observed in a real run on this Windows laptop and recorded below), `NOT_CONFIGURED` (operator-supplied configuration absent), `NOT_VALIDATED` (no authorized representative evaluation completed), `BLOCKED_BY_EXTERNAL_DEPENDENCY` (cannot proceed without an outside input).

- **IMPLEMENTED / TESTED:** Migration-owned FastAPI/SQLAlchemy backend, real webcam discovery and dashboard preview through the shared capture buffer, RTSP/file ingestion, explicit-path Ultralytics adapter, visual-only tracking, temporal verification, event-linked evidence and correlation, audited event/incident/near-miss/corrective-action lifecycles, the alarm subsystem (persisted `alarms` / `alarm_state_transitions`, audited state machine, policy, cooldown, repeat window, auto-expiry), the physical alarm transport architecture, the notification template engine, and truthful health states.
- **NOT_IMPLEMENTED:** Helmet, PPE, safety-vest, proximity, fall, leakage and unsafe-behaviour detection. These are absence or classification claims this build does not make. `GET /api/v1/system/detectors` reports them as `implementation_state=NOT_IMPLEMENTED` with `availability_state=NOT_AVAILABLE`. They are not merely unconfigured; they do not exist in this code.
- **IMPLEMENTED, NOT OPERATIONAL:** `RESTRICTED_ZONE`, `FIRE`, `SMOKE`, `PERSON` and `PHONE` detectors report `implementation_state=IMPLEMENTED` and `availability_state=MODEL_NOT_CONFIGURED` on this machine. No detection, track, safety event, incident or alarm has ever been produced here because no model weights exist.
- **NOT_CONFIGURED (model):** No weights are present in this repository and none were downloaded. `scripts/validate_model.py` returned `overall_state=MODEL_NOT_CONFIGURED`, every check `NOT_RUN`, `validation_state=NOT_VALIDATED`, `detection_quality_validated=false`, exit code 2. Local discovery found 0 candidate checkpoints.
- **NOT_CONFIGURED (email):** `POST /api/v1/notifications/test/email` returned `status=EMAIL_NOT_CONFIGURED`, `delivery_status=EMAIL_NOT_CONFIGURED`, reason "SMTP host and sender address are required". No SMTP credentials exist on this machine.
- **NOT_CONFIGURED (WhatsApp):** `POST /api/v1/notifications/test/whatsapp` returned `status=WHATSAPP_NOT_CONFIGURED`, `delivery_status=WHATSAPP_NOT_CONFIGURED`, reason "Cloud API token, phone number ID and API version are required". No Cloud API credentials exist on this machine.
- **NOT_CONFIGURED (physical alarm):** `POST /api/v1/alarms/physical/test` returned `PHYSICAL_ALARM_NOT_CONFIGURED` with `hardware_verified=false`. No relay, siren, GPIO line or industrial controller is connected to this laptop. No physical alarm was activated and none is claimed.
- **IMPLEMENTED WITH DEPLOYMENT LIMITS:** Password login, bcrypt password storage, signed short-lived JWT bearer tokens, role/permission checks, login throttling, one-time initial-admin provisioning, and self-service password changes. MFA, password recovery and centralised rate limiting are **NOT_IMPLEMENTED**. Development still defaults to anonymous mode for localhost only; production/staging require anonymous access disabled and a JWT signing secret.
- **NOT_VALIDATED:** IGL plant validation is `NOT_CONFIGURED` / `NOT_VALIDATED`. No IGL footage, layout, SOP or labelled dataset exists in this workspace. No substitute data has been created.
- **BLOCKED_BY_EXTERNAL_DEPENDENCY:** Real inference, detection, tracking, event, incident and live-alarm validation all require an authorised compatible checkpoint. IGL validation additionally requires authorised IGL footage, layout/SOPs and labelled evaluation data.
- **NOT_VALIDATED (production deployment):** TLS termination, reverse proxy, PostgreSQL and secrets-manager deployment are documented in [docs/deployment.md](docs/deployment.md) but were not exercised on this machine. Production readiness is not claimed.

No authorised IGL camera footage, plant layout, PPE SOP dataset, model checkpoint, or labelled IGL validation dataset is present in this workspace.

## Capability Table

| Area | Status | Evidence | Remaining dependency |
| --- | --- | --- | --- |
| Backend, migrations, health semantics | IMPLEMENTED / TESTED | `python -m pytest -q` from `backend/`: 365 passed, 0 failed | None |
| Webcam capture and camera lifecycle | IMPLEMENTED / REAL-WORLD VALIDATED | Registered "Laptop Webcam" opened through DSHOW, 165 real frames, `camera_state=CAMERA_AVAILABLE`, `camera_fps=8.03` | None for laptops; RTSP/file behaviour unvalidated |
| Operator-stop persistence across restart | IMPLEMENTED / TESTED | `data/camera_operator_stops.json` marker; camera checked at +8 s, +18 s, +28 s stayed stopped with 0 frames | None |
| Alarm subsystem (raise, suppress, acknowledge, escalate, clear, expire, history) | IMPLEMENTED / TESTED | 23 automated tests in `backend/tests/test_alarm_subsystem.py`; `GET /api/v1/alarms/policy` reported 0 alarms | A confirmed event requires model weights |
| Live alarm raised on this machine | NOT_CONFIGURED | `GET /api/v1/alarms` returned 0 rows; no confirmed event has ever existed here | Authorised compatible checkpoint |
| Physical alarm actuation | NOT_CONFIGURED | `POST /api/v1/alarms/physical/test` returned `PHYSICAL_ALARM_NOT_CONFIGURED`, `hardware_verified=false` | Real actuator hardware plus `paho-mqtt` / `pyserial` / `RPi.GPIO` where the transport needs them |
| Detectors `RESTRICTED_ZONE`, `FIRE`, `SMOKE`, `PERSON`, `PHONE` | IMPLEMENTED / NOT_CONFIGURED | `GET /api/v1/system/detectors` reported `IMPLEMENTED` / `MODEL_NOT_CONFIGURED` | Model weights exposing the required classes, plus detector/zone configuration |
| Detectors `HELMET`, `PPE`, `SAFETY_VEST`, `PROXIMITY`, `FALL`, `LEAKAGE`, `UNSAFE_BEHAVIOR` | NOT_IMPLEMENTED | `GET /api/v1/system/detectors` reported `NOT_IMPLEMENTED` / `NOT_AVAILABLE` | Implementation work; PPE-family also needs an absence-capable model |
| Model artifact validation CLI | IMPLEMENTED / TESTED | `python scripts/validate_model.py` returned `MODEL_NOT_CONFIGURED`, exit 2, every check `NOT_RUN` | A real checkpoint to validate |
| Local model discovery | IMPLEMENTED / TESTED | Discovery found 0 candidate checkpoints; no download and no auto-activation | An operator-placed checkpoint |
| Notification delivery (EMAIL, WHATSAPP) | IMPLEMENTED / NOT_CONFIGURED | Test sends returned `EMAIL_NOT_CONFIGURED` and `WHATSAPP_NOT_CONFIGURED` | SMTP credentials; Meta Cloud API credentials |
| Notification templates | IMPLEMENTED / TESTED | Fixed placeholder set; unknown placeholders reported `NOT_SUBSTITUTED` | None |
| Browser dashboard | IMPLEMENTED / REAL-WORLD VALIDATED | Microsoft Edge run: 11 views at 390/768/1024/1440/1920 px, zero horizontal overflow, zero console errors, zero failed requests, webcam start/stop/reopen/stop cycle `VALIDATED` | None |
| JWT authentication and RBAC | IMPLEMENTED WITH LIMITS | Roles seeded; permission gates on every alarm route | MFA, recovery, centralised rate limiting not implemented |
| IGL configuration | NOT_CONFIGURED | `config/igl/*.yaml` all carry `configuration_status: NOT_CONFIGURED` and `records: []` | Authorised IGL records |
| Production deployment | NOT_VALIDATED | Documented only, in [docs/deployment.md](docs/deployment.md) | Validation in the target environment |

## Real Runs On This Machine

Every figure below was observed on this Windows laptop and is not extrapolated.

- **Automated suite:** `python -m pytest -q` from `backend/` returned **365 passed, 0 failed** in 82.68 s (it was 228 before this work). The suite migrates a temporary database with the real Alembic chain and never touches the operator database.
- **Static checks:** `python -m compileall` clean; `node --check frontend/js/app.js` and `node --check scripts/smoke_browser.mjs` pass; all four `scripts/*.ps1` files parse without errors; `git diff --check` clean.
- **Webcam (real device):** `GET /api/v1/cameras/webcam/devices` found 6 probe indices with device 0 reporting 640x480. Starting the registered "Laptop Webcam" opened the physical USB camera through the DSHOW backend, delivered 165 real frames, and the health layer reported `camera_state=CAMERA_AVAILABLE` with `measured_performance=MEASURED_FROM_OBSERVED_FRAMES` and `camera_fps=8.03`. Stop returned `NOT_STARTED`; the camera was checked at +8 s, +18 s and +28 s (health interval is 5 s) and stayed stopped with 0 frames, and health correctly dropped to `CAMERA_CONFIGURED_NOT_OBSERVED`. Reopening returned to `RUNNING` with 67 frames through DSHOW; a final stop returned `NOT_STARTED`. `reconnect_state=IDLE` throughout.
- **Browser:** `node scripts/smoke_browser.mjs` (Microsoft Edge) rendered 11 views at 390/768/1024/1440/1920 px, with zero horizontal overflow after the CSS fix, zero browser console errors, zero failed requests, and the webcam start/stop/reopen/stop cycle reported `VALIDATED` through the real UI.
- **HTTP smoke:** `scripts/test_frontend_smoke.ps1` returned PASS: dashboard HTML, navigation, JS/CSS delivery, health endpoint and worker-track API all responded. The workers endpoint returned 0 records because no model has ever produced a track.
- **Health:** `overall_status` is `DEGRADED` with `degraded_reasons` containing `MODEL_NOT_CONFIGURED` (and `CAMERA_CONFIGURED_NOT_OBSERVED` when the camera is stopped). That is the truthful state and is not treated as a fault to hide.

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

Windows convenience scripts: `./scripts/start_backend.ps1` and `./scripts/start_frontend.ps1` (both bind to loopback by default; the backend script checks dependencies and applies Alembic migrations before starting). `./scripts/validate_system.ps1` runs a physical webcam probe and the full test suite without sending test notifications or changing the configured database.

## Validating A Model

`scripts/validate_model.py` validates a model artifact without any video source. It reports each check separately (`FILE_EXISTS`, `FILE_READABLE`, `MODEL_LOADS`, `CLASSES_PRESENT`, `INFERENCE_EXECUTES`, `OUTPUT_SCHEMA_VALID`, `LATENCY_MEASURED`), never marks an unreached check as passed, and exits 0 on success, 1 on a check that ran and failed, 2 when no check could run at all.

```powershell
python scripts/validate_model.py
python scripts/validate_model.py --weights "C:\authorized\path\checkpoint.pt" --timed-passes 10
```

On this machine the command returned `overall_state=MODEL_NOT_CONFIGURED`, `validation_state=NOT_VALIDATED`, every check `NOT_RUN`, `detection_quality_validated=false`, exit code 2. The blank-frame throughput probe is labelled `SYNTHETIC_PROBE_NOT_REAL_FOOTAGE` and says nothing about detection accuracy. `.onnx` and `.engine` checkpoints require `onnxruntime` and `tensorrt` respectively; neither is installed here, and a missing runtime is reported as `MODEL_RUNTIME_DEPENDENCY_MISSING` with the package named.

## Alarm Centre

The alarm centre is served at `/api/v1/alarms`. An alarm is only ever raised from a persisted, temporally confirmed safety event, either by the safety orchestrator after the event is persisted or by an explicitly authorised operator through `POST /api/v1/alarms/events/{event_id}/raise`, which still refuses a non-confirmed event.

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/alarms?active_only=true
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/alarms/policy
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/alarms/physical/status
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/alarms/physical/test
```

`GET /api/v1/alarms/policy` on this machine reported the policy enabled, `min_severity` `MEDIUM`, 120 s cooldown, 3 repeats per window, and 0 alarms, because no confirmed event has ever been produced. `GET /api/v1/alarms` returned 0 rows. Alarm behaviour is covered by 27 automated tests, not by a live alarm. Full endpoint and permission detail is in [docs/api.md](docs/api.md); day-to-day handling is in [docs/operations.md](docs/operations.md).

## Health Semantics

`GET /` and `GET /api/v1/system/health` report observation rather than assumption. `APPLICATION_UP` states only that the process is serving; it is separate from `DATABASE_OK`/`DATABASE_UNAVAILABLE`, `MIGRATIONS_CURRENT`/`MIGRATIONS_PENDING_OR_UNAVAILABLE`, `MODEL_CONFIGURED`/`MODEL_NOT_CONFIGURED`, and `NO_CAMERA`/`CAMERA_CONFIGURED_NOT_OBSERVED`/`CAMERA_AVAILABLE`/`CAMERA_RECORDED_BUT_NONE_ACTIVE`. A camera is `CAMERA_AVAILABLE` only when its active running stream has delivered a real frame. A pipeline is `RUNNING` only after a completed inference. `measured_performance` is `MEASURED_FROM_OBSERVED_FRAMES` or `NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`; no FPS, latency or accuracy figure is reported unless it was measured from a real observed frame. Uptime is measured (`uptime_state=MEASURED`), disk is measured from the real volume (`DISK_OK` / `DISK_FREE_SPACE_BELOW_THRESHOLD` / `DISK_UNAVAILABLE`), and transport configuration is reported as `EMAIL_CONFIGURED`/`EMAIL_NOT_CONFIGURED` and `WHATSAPP_CONFIGURED`/`WHATSAPP_NOT_CONFIGURED` without implying delivery. Every response carries an `X-Request-ID` correlation header.

## Tests

```powershell
cd backend
python -m pytest -q
```

365 passed, 0 failed on this machine. The suite migrates a temporary database with the real Alembic chain and never touches your configured database. See [docs/setup.md](docs/setup.md).

## Run Real-Input Validation

Provide one authorised source (`VIDEO_SOURCE` for a local video or `RTSP_URL` for an IP stream) and a readable local checkpoint with explicit `MODEL_NAME` and `MODEL_VERSION` in `.env`. Set `MODEL_CONFIDENCE_THRESHOLD` for that model. Set `CAMERA_URL_ENCRYPTION_KEY` before adding credential-bearing camera URLs. Alternatively, pass a local file or credential-free URL with `--source`.

```powershell
python scripts/run_inference.py --source "C:\authorized\path\sample.mp4"
python scripts/run_inference.py --duration-seconds 30
```

The second command reads `RTSP_URL` from the environment without echoing it. A JSON report is written under `data/validation_reports/`, which is git-ignored. Reports record `input_provenance`; the CLI always reports `AUTHORIZED_REAL_INPUT`, and a report claiming `REAL_INPUT_VALIDATED` is rejected unless real frames and a completed inference were observed with no errors. The test suite runs with `TEST_FIXTURE` provenance and is recorded as `TEST_FIXTURE_EXECUTION`, never as validation. No model weights are downloaded. See [docs/validation.md](docs/validation.md).

## Scope

The platform is advisory only and never controls PLCs, machinery, valves, interlocks or emergency systems. Camera failures are recorded as `CAMERA_FAILURE` with `NOT_ASSESSABLE`; supported safety events can only come from real model detections through configured detectors and zones. The browser audible alarm is driven by the real active-alarm list and is an operator-controlled sound, not a physical output. See [docs/architecture.md](docs/architecture.md), [docs/api.md](docs/api.md), and [docs/ai_validation.md](docs/ai_validation.md).

The frontend provides overview, camera, worker, event, incident, alarm centre, rules, notification, evidence, analytics, system health, configuration and audit views. The Workers page reads persisted visual track/detection/event records through `GET /api/v1/workers/tracks` and joins linked incidents from `GET /api/v1/events`; it does not expose employee identity and never claims an area is safe. The Rules page shows per-detector implementation state, availability state and validation state from `GET /api/v1/system/detectors`. The Notifications page shows the delivery-status vocabulary and prints the exact status returned by each test send. Browser camera frames remain local to the browser and are not connected to backend inference. See [docs/igl_configuration.md](docs/igl_configuration.md), [docs/api.md](docs/api.md), and [docs/operations.md](docs/operations.md).
