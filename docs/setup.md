# Setup

## Development

From the project root, install the pinned dependencies, copy `.env.example` to `.env`, then run the migration and API:

```powershell
python -m pip install -r backend/requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
python -m alembic -c backend/alembic.ini upgrade head
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

The API refuses to start unless the database revision matches the Alembic head. Schema is owned exclusively by the migrations; `init_db()` no longer creates tables and raises instead.

The example config enables anonymous development mode. Keep the API bound to localhost; do not expose it to an untrusted network. To enable login locally or on a shared deployment, set `ALLOW_ANONYMOUS_ACCESS=false` and configure `AUTH_JWT_SECRET_KEY`.

The default SQLite location resolves relative to the project root. Migrations add schema to an existing database; do not delete, recreate, or reset a database as a setup step.

Run the static dashboard from a second project-root terminal with `python -m http.server 8001 --directory frontend`; open `http://127.0.0.1:8001`.

## Tests

```powershell
cd backend
python -m pytest -q
```

365 passed, 0 failed on this machine. `backend/tests/conftest.py` creates a temporary file database, migrates it with the real Alembic chain, and points the application at it before any application module is imported. The suite therefore never reads, writes, or drops your configured database.

## Environment Settings

`.env.example` documents every setting. The groups below are the ones added or extended most recently.

### Model

- `MODEL_WEIGHTS_PATH`: the explicit local checkpoint. `.pt`, `.onnx` and `.engine` are supported. Nothing is ever downloaded.
- `MODEL_NAME`, `MODEL_VERSION`, `MODEL_CONFIDENCE_THRESHOLD`, `MODEL_DEVICE`, `MODEL_IOU_THRESHOLD`, `MODEL_IMAGE_SIZE`: traceable model metadata and behaviour.
- `MODEL_AUTO_DISCOVER` (default `false`) and `MODEL_SEARCH_PATHS`: local discovery of checkpoints an operator already placed on the machine. `MODEL_AUTO_DISCOVER=true` requires at least one search path, and the settings validator refuses to start otherwise. `MODEL_SEARCH_MAX_DEPTH` (1–8, default 3) bounds how deep discovery descends.
- `MODEL_VALIDATION_WARMUP_PASSES` (default 2) and `MODEL_VALIDATION_TIMED_PASSES` (default 5): used by `scripts/validate_model.py` so reported latency is a steady-state measurement rather than a first-call artefact.
Format runtimes are checked, never installed. A `.pt` checkpoint needs `ultralytics` and `torch`, a `.onnx` checkpoint also needs `onnxruntime`, and a `.engine` checkpoint also needs `tensorrt`. A missing runtime is reported as `MODEL_RUNTIME_DEPENDENCY_MISSING` with the package named in `missing_runtime_packages`. `onnxruntime` and `tensorrt` are not installed on this machine.

### Notification templates

`EMAIL_INCIDENT_SUBJECT_TEMPLATE`, `EMAIL_INCIDENT_BODY_TEMPLATE`, `EMAIL_ESCALATION_SUBJECT_TEMPLATE`, `EMAIL_ESCALATION_BODY_TEMPLATE`, `EMAIL_TEST_BODY_TEMPLATE`, `WHATSAPP_INCIDENT_TEMPLATE`, `WHATSAPP_ESCALATION_TEMPLATE`, `WHATSAPP_TEST_TEMPLATE`. Templates are plain text. Only the fixed placeholder set can be substituted; an unknown placeholder is left visible and reported as `NOT_SUBSTITUTED` rather than filled. See [docs/architecture.md](docs/architecture.md) for the placeholder list and the sentinel values.

### Alarm policy

`ALARM_ENABLED` (default `true`), `ALARM_MIN_SEVERITY` (`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`, default `MEDIUM`), `ALARM_COOLDOWN_SECONDS` (default 120), `ALARM_MAX_REPEATS_PER_WINDOW` (default 3), `ALARM_REPEAT_WINDOW_SECONDS` (default 900), `ALARM_AUTO_EXPIRE_SECONDS` (default 1800), `ALARM_AUDIBLE_BROWSER` (default `true`), `ALARM_PHYSICAL_ACTUATION` (default `false`).

### Physical alarm transports

All opt-in and all defaulting to unconfigured, which health reports as `PHYSICAL_ALARM_NOT_CONFIGURED`:

- HTTP: `PHYSICAL_ALARM_HTTP_URL` (secret-typed), `PHYSICAL_ALARM_HTTP_METHOD` (`POST`/`PUT`), `PHYSICAL_ALARM_HTTP_BEARER_TOKEN` (secret-typed), `PHYSICAL_ALARM_HTTP_TIMEOUT_SECONDS`.
- MQTT: `PHYSICAL_ALARM_MQTT_HOST`, `PHYSICAL_ALARM_MQTT_PORT`, `PHYSICAL_ALARM_MQTT_TOPIC` (host requires topic), optional paired `PHYSICAL_ALARM_MQTT_USERNAME`/`PHYSICAL_ALARM_MQTT_PASSWORD`, `PHYSICAL_ALARM_MQTT_USE_TLS`. Requires `paho-mqtt`.
- Serial: `PHYSICAL_ALARM_SERIAL_PORT`, `PHYSICAL_ALARM_SERIAL_BAUD`, `PHYSICAL_ALARM_SERIAL_PULSE_SECONDS`. Requires `pyserial`.
- GPIO: `PHYSICAL_ALARM_GPIO_PIN` (0 means unconfigured), `PHYSICAL_ALARM_GPIO_ACTIVE_HIGH`. Requires `RPi.GPIO`.

A configured transport still reports `ACTUATOR_NOT_CONNECTED` until real hardware answers.

### System health

`HEALTH_DISK_PATH` (defaults to the evidence directory), `HEALTH_DISK_MIN_FREE_BYTES` (default 536870912), `HEALTH_DISK_MIN_FREE_PERCENT` (default 5.0). The disk figure is measured from the real volume; an unmeasurable volume is `DISK_UNAVAILABLE`, not a healthy default.

### Per-rule safety parameters

`SAFETY_RULE_DEBOUNCE_SECONDS` (default 5) and `SAFETY_RULE_COOLDOWN_SECONDS` (default 60) are the fallbacks used when a detector rule does not set its own values in `DetectorConfig.parameters_json`. A rule configured with `debounce_seconds` or `cooldown_seconds` overrides them. Debounce is the minimum spacing between two new candidate events for the same rule, zone and track; cooldown suppresses re-raising a rule for a track after an event has already been raised for it. Both exist so a single sustained condition does not produce an incident storm.

### PostgreSQL

`DATABASE_URL` accepts any SQLAlchemy URL. Outside `development`/`test`, a SQLite URL is rejected at startup. A PostgreSQL example is in `.env.example`:

```
DATABASE_URL=postgresql+psycopg://igl_safety:CHANGE_ME@db.internal:5432/igl_safety
```

The matching driver is not in `backend/requirements.txt` and must be installed and reviewed separately. `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_RECYCLE_SECONDS`, `DATABASE_STATEMENT_TIMEOUT_MS` and `DATABASE_CONNECT_TIMEOUT_SECONDS` tune pooling and timeouts. See [docs/deployment.md](docs/deployment.md); that topology has not been validated here.

## Model Validation Step

Validate an artifact before wiring a camera to it. No video source is required.

```powershell
python scripts/validate_model.py
python scripts/validate_model.py --weights "C:\authorized\path\checkpoint.pt" --report "C:\secure\model_report.json"
```

The command reports `FILE_EXISTS`, `FILE_READABLE`, `MODEL_LOADS`, `CLASSES_PRESENT`, `INFERENCE_EXECUTES`, `OUTPUT_SCHEMA_VALID` and `LATENCY_MEASURED` independently, marks every unreached check `NOT_RUN`, and exits 0 (all passed), 1 (a check ran and failed) or 2 (no check could run). With no `MODEL_WEIGHTS_PATH` it reports `overall_state=MODEL_NOT_CONFIGURED` and exits 2, which is what it does on this machine. The blank-frame throughput probe is labelled `SYNTHETIC_PROBE_NOT_REAL_FOOTAGE` and never raises `validation_state` above `NOT_VALIDATED`. Reports default to `data/validation_reports/`, which is git-ignored; keep them access-controlled.

Local discovery, if enabled, reports checkpoints that already exist with their real size and SHA-256 and marks each `NOT_ACTIVATED`. It never downloads. A model runs only when `MODEL_WEIGHTS_PATH` names it.

## Alarm Prerequisites

Before an alarm can be raised at all, three things must exist. None of them exists on this machine.

1. A real, loaded model. `GET /api/v1/system/health` must stop reporting `MODEL_NOT_CONFIGURED`. An alarm is raised only from a persisted, temporally confirmed safety event, and an event requires a real detection.
2. A detector configuration and, for zone-scoped detectors, configured zone geometry. A supported detector with real output and configuration is what produces the event; without a zone, a detector that needs one reports it rather than guessing.
3. A confirmed event. Temporal verification must have moved the event to `CONFIRMED`. A `NOT_ASSESSABLE` or unverified event never raises an alarm, and neither does an operator request against it.

Given those, `ALARM_ENABLED` must be `true` and the event severity must be at or above `ALARM_MIN_SEVERITY`, and no cooldown or repeat limit may be in force.

For physical actuation, add a configured transport, its driver library, and real hardware. The full commissioning sequence is in [docs/operations.md](docs/operations.md). Until that is done, `POST /api/v1/alarms/physical/test` returns `PHYSICAL_ALARM_NOT_CONFIGURED` with `hardware_verified=false`, and that is the correct answer.

## Email And WhatsApp Notifications

SMTP settings are `SMTP_HOST`, `SMTP_PORT`, optional paired `SMTP_USERNAME`/`SMTP_PASSWORD`, `SMTP_USE_TLS` (STARTTLS), `SMTP_USE_SSL` (implicit TLS), `SMTP_TIMEOUT_SECONDS`, `NOTIFICATION_FROM_ADDRESS`, and `NOTIFICATION_RECIPIENTS` (JSON string array). TLS modes are mutually exclusive, and username/password must be set together or not at all. For WhatsApp Cloud API, configure `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_API_VERSION` (the Graph API version enabled for your Meta app), `WHATSAPP_TIMEOUT_SECONDS`, and `WHATSAPP_RECIPIENTS` (JSON string array of E.164 numbers). Use `.env` or a secret manager; never put tokens in frontend code. The worker starts with the API and attempts queued delivery with bounded retries. Missing settings remain `NOT_CONFIGURED`.

The WhatsApp sender uses Meta's official `POST /{version}/{phone-number-id}/messages` Cloud API request shape; see [Meta's WhatsApp Cloud API collection](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api?entity=request-13382743-071cfa60-0704-41d2-bca2-36ba6bd33dfe).

PowerShell test sends (only to recipients approved to receive an actual test message):

```powershell
$payload = @{ recipient = "operator@example.com" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/notifications/test/email -ContentType "application/json" -Body $payload
$payload = @{ recipient = "+15551234567" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/notifications/test/whatsapp -ContentType "application/json" -Body $payload
```

On this machine both calls return `*_NOT_CONFIGURED` with an explicit reason. No SMTP or Cloud API credentials exist here.

## Deployment Boundary

Production/staging reject `ALLOW_ANONYMOUS_ACCESS=true` and reject SQLite. Authenticated mode requires a random `AUTH_JWT_SECRET_KEY` of at least 32 bytes and an active admin account. Generate the signing key with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

For first startup, set `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_FULL_NAME`, and `INITIAL_ADMIN_PASSWORD` in a protected environment/secret manager. The password must be 12–72 UTF-8 bytes. The API creates this account only if the database has no ADMIN; remove the bootstrap password after provisioning. Login is available at `/api/v1/auth/login`; the frontend presents a sign-in screen automatically when the backend reports authenticated mode. Users can change their password at `/api/v1/auth/password`.

Reference roles are seeded idempotently at startup: `VIEWER` has read-only operational permissions and no live-camera permission; `SUPERVISOR` can view cameras, acknowledge/assign events, and review/manage response workflows. Neither role can administer accounts or edit plant configuration. Existing role rows are preserved and are not silently rewritten by reseeding.

After both services are up, run `./scripts/test_frontend_smoke.ps1` in a third PowerShell terminal. For an authenticated deployment, set `IGL_API_TOKEN` in that terminal before running the script; the token is used only as an Authorization header and is not printed. The smoke check makes GET requests only. Interactive browser rendering, responsive layout, and camera preview still require an actual browser session.

For Edge-based browser validation, use Node.js 22+ and run `node scripts/smoke_browser.mjs`. It opens the actual served UI and checks each of the 11 visible operational pages at 390/768/1024/1440/1920-pixel viewports, failing on horizontal overflow, browser console errors or failed requests. Set `IGL_TEST_USERNAME` and `IGL_TEST_PASSWORD` to test a configured login/logout flow. It requests camera start/stop/reopen only when the Cameras view discovers a real backend webcam; it never fabricates frames. It does not submit email/WhatsApp tests or create incidents.

JWT authentication and role/permission checks are implemented. This repository does not bundle a production database driver/topology, secret manager, TLS/reverse proxy, distributed rate limiter, recovery flow, MFA, or independent security review. Use an installed SQLAlchemy driver, exact frontend origins, and reviewed TLS/network controls. See [docs/deployment.md](docs/deployment.md) and [docs/security.md](docs/security.md).

## Camera URL Encryption

Credential- or query-bearing camera URLs require `CAMERA_URL_ENCRYPTION_KEY`. Generate a Fernet key locally and store it only in a secret manager or `.env`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the resulting value as `CAMERA_URL_ENCRYPTION_KEY` before creating such cameras. Losing or changing this key makes encrypted camera sources unreadable; perform key rotation with a separately planned, backed-up migration. Existing legacy camera URLs stored before encryption may require a controlled re-encryption procedure; do not assume old rows were encrypted retroactively.
