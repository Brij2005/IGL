# Deployment

**Production readiness is NOT claimed.** Everything in this file is a documented procedure. None of it was executed on the machine this repository was validated on. That machine runs Windows, SQLite, anonymous localhost development mode, a laptop webcam, and no model weights. The staging and production sections describe what must be configured and then validated in the target environment before this software is relied on.

## Local Windows (Validated)

This is the only configuration that has actually been exercised here.

```powershell
python -m pip install -r backend/requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
python -m alembic -c backend/alembic.ini upgrade head
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal: `python -m http.server 8001 --directory frontend`, then open `http://127.0.0.1:8001`.

- `ENVIRONMENT=development` and `ALLOW_ANONYMOUS_ACCESS=true` from `.env.example`. Bind to loopback only. Anonymous access is rejected automatically outside `development`/`test`.
- `DATABASE_URL=sqlite:///data/database.db`. SQLite is rejected outside `development`/`test`.
- `BACKEND_CORS_ORIGINS` as a JSON list of exact loopback origins. A wildcard is rejected in every environment.
- `./scripts/start_backend.ps1` and `./scripts/start_frontend.ps1` do the same thing with dependency checks and migrations applied.

Observed here: 365 automated tests pass, the dashboard renders without overflow at five viewport widths, and a USB webcam delivers real frames through DSHOW. No model, no SMTP, no WhatsApp and no actuator are configured, so `overall_status` is `DEGRADED` with `MODEL_NOT_CONFIGURED`.

## Staging (Not Validated)

Staging is where the first real integrations are exercised, in an environment that is not the plant network. Treat it as a rehearsal for production and do not skip the validation steps.

### Database

```dotenv
ENVIRONMENT=staging
ALLOW_ANONYMOUS_ACCESS=false
AUTH_JWT_SECRET_KEY=<at least 32 random bytes>
DATABASE_URL=postgresql+psycopg://igl_safety:<password>@db.internal:5432/igl_safety
```

Install the driver separately; it is not in `backend/requirements.txt` and must be reviewed before it goes near a network:

```powershell
python -m pip install "psycopg[binary]"
```

Tune pooling for the deployment: `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_RECYCLE_SECONDS`, `DATABASE_STATEMENT_TIMEOUT_MS`, `DATABASE_CONNECT_TIMEOUT_SECONDS`. Back up before every migration. Never delete or recreate a database to apply a migration; use `python -m alembic -c backend/alembic.ini upgrade head`.

Verify after migration: `GET /api/v1/system/health` must report `database=DATABASE_OK` and `migrations=MIGRATIONS_CURRENT`. Anything else is `MIGRATIONS_PENDING_OR_UNAVAILABLE` and must be resolved before traffic.

### Authentication And Bootstrap

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `AUTH_JWT_SECRET_KEY` from that output, and for first startup set all four of `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_FULL_NAME`, `INITIAL_ADMIN_PASSWORD` (12–72 UTF-8 bytes). Startup creates that account only if no ADMIN exists. Remove the bootstrap password from the environment immediately afterwards; if it is removed without an admin existing, the next startup will refuse with "Authentication requires an active ADMIN".

### CORS

Set `BACKEND_CORS_ORIGINS` to the exact staging frontend origins, as a JSON list. Do not use `*`; startup rejects it. Restart the API after changing it, and confirm from a browser on that origin that requests succeed and requests from an unlisted origin fail.

### TLS And Reverse Proxy

Terminate TLS in front of the API. The application does not terminate TLS itself; `Strict-Transport-Security` is only added on requests that already arrive over HTTPS, so without a proxy it is never sent.

- The API binds to loopback or a private interface. The proxy is the only component exposed to the network.
- Serve the static frontend from the same origin, or add that exact origin to `BACKEND_CORS_ORIGINS`.
- Add an origin-specific Content-Security-Policy at the proxy. The application's CSP intentionally leaves resource loading and API origins to the deployment topology.
- Restrict request body size and rate-limit the authentication endpoints at the proxy. Application-level login throttling is process-local and resets on restart.

Verify: an HTTPS request returns HSTS; the dashboard loads and signs in over TLS; a plaintext HTTP request to the API port from outside the host is refused.

### Secrets

Move every secret out of `.env` into a secret manager or a mounted, permission-restricted environment file. At minimum: `DATABASE_URL`, `AUTH_JWT_SECRET_KEY`, `RTSP_URL`, `CAMERA_URL_ENCRYPTION_KEY`, `SMTP_PASSWORD`, `WHATSAPP_ACCESS_TOKEN`, `PHYSICAL_ALARM_HTTP_BEARER_TOKEN`, `PHYSICAL_ALARM_MQTT_PASSWORD`. Confirm that none of them appears in a log line, an API response, a validation report or a repository. `PHYSICAL_ALARM_HTTP_URL` is secret-typed as well.

### Camera Network Addresses

Cameras live on the plant network, not on the host. For each camera:

- Register the stream URL through the API. Credential- or query-bearing URLs require `CAMERA_URL_ENCRYPTION_KEY`, generated per environment:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- The host must have network reach to each camera. Confirm before registering, then confirm by reading `GET /api/v1/cameras/{id}/status` and checking that `observed_frames` is true and `total_frames_read` is rising. `CAMERA_AVAILABLE` requires a running stream with an observed real frame.
- Size `CAMERA_CONNECT_TIMEOUT_SECONDS`, `CAMERA_READ_TIMEOUT_SECONDS`, `CAMERA_RECONNECT_INITIAL_DELAY_SECONDS`, `CAMERA_RECONNECT_MAX_DELAY_SECONDS` and `CAMERA_MAX_RECONNECT_ATTEMPTS` for the real link. `0` attempts means unlimited retries.
- Size `HEALTH_DISK_MIN_FREE_BYTES` and `HEALTH_DISK_MIN_FREE_PERCENT` for the volume that actually holds `EVIDENCE_DIR`, and size `EVIDENCE_RETENTION_DAYS` and `FRAME_BUFFER_*` for the expected frame rate and record count.
- Laptops are not cameras for a plant deployment. A `webcam://` registration is a developer convenience.

### Model And Alarms

No alarm can be raised without a model, an authorised compatible checkpoint, a detector configuration and a confirmed event. Before staging can demonstrate an alarm end to end, all of the following must be true: `MODEL_WEIGHTS_PATH` names a real checkpoint whose runtime is installed; `python scripts/validate_model.py --weights <path>` reports `PASS` for all seven checks; detector and zone configuration exists; and the temporal thresholds are operator-set rather than `ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION`.

Commission the physical actuator as a separate, signed-off step following [operations.md](operations.md). Until `hardware_verified=true` from a real `POST /api/v1/alarms/physical/test`, treat the actuator as uncommissioned.

### Staging Validation Checklist

Record the result of each item. An unchecked box means the capability is NOT_VALIDATED.

- [ ] `GET /api/v1/system/health` reports `DATABASE_OK` and `MIGRATIONS_CURRENT`.
- [ ] Anonymous access is refused; sign-in and token use work; a role-restricted endpoint refuses an under-privileged account.
- [ ] TLS terminates at the proxy; HSTS is present; the API port is not reachable from outside the host.
- [ ] CORS allows the staging origin and rejects an unlisted one.
- [ ] No secret appears in any log, response or report.
- [ ] Every camera delivers observed frames; `camera_state=CAMERA_AVAILABLE` for each active camera.
- [ ] A camera an operator stopped stays stopped across a backend restart.
- [ ] `scripts/validate_model.py` passes all seven checks for the staging checkpoint.
- [ ] At least one detector is configured and produces a real event, and the event reaches `CONFIRMED`.
- [ ] An alarm is raised from that event; acknowledge, escalate and clear are exercised; `GET /api/v1/alarms/{id}/history` shows the audited transitions.
- [ ] Email and/or WhatsApp test sends return `*_DELIVERED` to an operator-approved recipient and the message is actually received.
- [ ] If an actuator is installed, `POST /api/v1/alarms/physical/test` returns `ACTIVATED` with `hardware_verified=true`, and the audit log holds the attempt.
- [ ] `uptime_seconds`, disk free space and measured camera/inference FPS read sensibly under real load, and `measured_performance` is `MEASURED_FROM_OBSERVED_FRAMES` rather than `NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`.

## Production (Not Validated)

Production is staging plus the controls below. Every item must be re-verified in the production environment; results do not transfer from staging.

### Required Before Any Plant Use

1. **Authorised inputs.** An authorised model checkpoint, authorised IGL zone geometry, approved detector thresholds with a documented source reference, approved escalation tiers, and approved notification recipients. All of `config/igl/*.yaml` is still `NOT_CONFIGURED` today.
2. **Database.** Managed PostgreSQL with automated backups, a tested restore, connection limits sized for the deployment, and TLS to the database. Verify a restore, not just a backup.
3. **TLS And Network.** TLS 1.2 or later at a reviewed reverse proxy, HSTS, no plaintext API exposure, network segmentation between the API host and the camera network, and egress restricted to the SMTP relay, the Meta API and any configured actuator endpoint.
4. **Secrets.** All secrets in a managed secret store with rotation planned. `AUTH_JWT_SECRET_KEY` rotation invalidates all sessions; plan it. `CAMERA_URL_ENCRYPTION_KEY` loss makes stored camera URLs permanently unreadable; back it up separately and rotate with a planned migration.
5. **Access Control.** Anonymous access disabled. MFA, password recovery and centralised rate limiting are **NOT_IMPLEMENTED** in this codebase, so compensating controls are mandatory: restrict access to the plant network, front the authentication endpoints with a proxy rate limit, and plan a manual account-recovery procedure. No security certification or penetration test has been performed.
6. **Process Model.** Camera health polling, notification delivery and pipeline state are process-local. A multi-worker deployment without a shared scheduler duplicates the polling and the delivery loop. Either run a single API process, or add a shared scheduler role and a shared broker before scaling out. That topology has not been validated. Alarm expiry is request-driven rather than a background job, so it does not need a scheduler, but a multi-worker deployment will still have several processes able to expire alarms.
7. **Monitoring.** Alert on `overall_status` moving off `DEGRADED` into a worse state, on `degraded_reasons` gaining an entry, on `disk_state` leaving `DISK_OK`, on `MIGRATIONS_PENDING_OR_UNAVAILABLE`, and on `notification_delivery_worker` not being `RUNNING`. Treat `MODEL_NOT_CONFIGURED` and `CAMERA_CONFIGURED_NOT_OBSERVED` as pages only if the deployment is supposed to be running.
8. **Backup And Retention.** Back up the database and `EVIDENCE_DIR` on a schedule and test the restore. Apply `EVIDENCE_RETENTION_DAYS` only after a retention decision has been approved in `config/igl/notification_recipients.yaml`.
9. **Change Control.** Schema changes are additive Alembic migrations only. Take a backup, apply to a copy, verify upgrade and downgrade, then apply to production.
10. **Independent Review.** An independent penetration test, security review and IGL safety review of the detection thresholds have not been performed. The platform is advisory only and never controls PLCs, machinery, valves, interlocks or emergency systems; it must not be wired into a safety instrumented function.

### What Production Does Not Change

- `validation_status` stays `NOT_VALIDATED` and `igl_validated` stays `false` until a reviewed IGL study is completed against authorised footage. Deploying to production does not make the detection thresholds IGL-validated.
- `PPE`, `HELMET`, `SAFETY_VEST`, `PROXIMITY`, `FALL`, `LEAKAGE` and `UNSAFE_BEHAVIOR` remain `NOT_IMPLEMENTED`. They will not start working in production.
- A configured email or WhatsApp channel is not a delivered message, and a configured actuator is not an activated one. The delivery and physical states are the only evidence.

## Environment Checklist

| Item | Local Windows | Staging | Production |
| --- | --- | --- | --- |
| `ENVIRONMENT` | `development` | `staging` | `production` |
| `ALLOW_ANONYMOUS_ACCESS` | `true` (loopback only) | `false` | `false` |
| `AUTH_JWT_SECRET_KEY` | unset | 32+ random bytes | 32+ random bytes, rotated |
| `DATABASE_URL` | SQLite | PostgreSQL | Managed PostgreSQL, TLS, backups |
| `BACKEND_CORS_ORIGINS` | loopback list | exact staging origins | exact production origins |
| TLS | none (loopback) | proxy | proxy, HSTS, reviewed config |
| Secrets | `.env`, git-ignored | secret manager | secret manager, rotation planned |
| Cameras | laptop webcam | RTSP on staging network | plant network, segmented |
| Model | none | authorised checkpoint | authorised checkpoint |
| Alarms | never raised | raised in a test | raised from real confirmed events |
| Physical actuator | `PHYSICAL_ALARM_NOT_CONFIGURED` | commissioned and verified | commissioned and verified |
| Email / WhatsApp | `*_NOT_CONFIGURED` | `*_DELIVERED` to an approved recipient | `*_DELIVERED`, monitored |
| MFA / recovery / rate limit | not implemented | proxy controls | proxy controls plus a manual recovery procedure |
| Status | VALIDATED (as described in [validation.md](validation.md)) | NOT_VALIDATED | NOT_VALIDATED |
