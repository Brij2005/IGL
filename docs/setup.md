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

## Tests

```powershell
python -m pytest backend/tests -q
```

`backend/tests/conftest.py` creates a temporary file database, migrates it with the real Alembic chain, and points the application at it before any application module is imported. The suite therefore never reads, writes, or drops your configured database.

## Model Configuration

Set `MODEL_WEIGHTS_PATH` to an existing local model file. `MODEL_NAME`, `MODEL_VERSION`, and `MODEL_CONFIDENCE_THRESHOLD` provide traceable metadata. The service does not download weights. An absent file yields `MODEL_NOT_CONFIGURED`; an invalid configured path yields `MODEL_INVALID_WEIGHTS`.

## Email and WhatsApp notifications

SMTP settings are `SMTP_HOST`, `SMTP_PORT`, optional paired `SMTP_USERNAME`/`SMTP_PASSWORD`, `SMTP_USE_TLS` (STARTTLS), `SMTP_USE_SSL` (implicit TLS), `SMTP_TIMEOUT_SECONDS`, `NOTIFICATION_FROM_ADDRESS`, and `NOTIFICATION_RECIPIENTS` (JSON string array). TLS modes are mutually exclusive. For WhatsApp Cloud API, configure `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_API_VERSION` (the Graph API version enabled for your Meta app), `WHATSAPP_TIMEOUT_SECONDS`, and `WHATSAPP_RECIPIENTS` (JSON string array of E.164 numbers). Use `.env` or a secret manager; never put tokens in frontend code. The worker starts with the API and attempts queued delivery with bounded retries. The dashboard Configuration page exposes real test-send forms; these contact their selected real recipient and are audit-recorded. Provider acceptance does not guarantee inbox/user receipt. Missing settings remain `NOT_CONFIGURED`.

The WhatsApp sender uses Meta's official `POST /{version}/{phone-number-id}/messages` Cloud API request shape; see [Meta's WhatsApp Cloud API collection](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api?entity=request-13382743-071cfa60-0704-41d2-bca2-36ba6bd33dfe).

PowerShell test sends (only to recipients approved to receive an actual test message):

```powershell
$payload = @{ recipient = "operator@example.com" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/notifications/test/email -ContentType "application/json" -Body $payload
$payload = @{ recipient = "+15551234567" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/notifications/test/whatsapp -ContentType "application/json" -Body $payload
```

## Deployment Boundary

Production/staging reject `ALLOW_ANONYMOUS_ACCESS=true`. Authenticated mode requires a random `AUTH_JWT_SECRET_KEY` of at least 32 bytes and an active admin account. Generate the signing key with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

For first startup, set `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_FULL_NAME`, and `INITIAL_ADMIN_PASSWORD` in a protected environment/secret manager. The password must be 12–72 UTF-8 bytes. The API creates this account only if the database has no ADMIN; remove the bootstrap password after provisioning. Login is available at `/api/v1/auth/login`; the frontend presents a sign-in screen automatically when the backend reports authenticated mode. Users can change their password at `/api/v1/auth/password`.

JWT authentication and role/permission checks are implemented. Production/staging reject SQLite URLs and wildcard CORS, but this repository does not bundle a production database driver/topology, secret manager, TLS/reverse proxy, distributed rate limiter, recovery/MFA, or independent security review. Use an installed SQLAlchemy driver, exact frontend origins, and reviewed TLS/network controls.

Reference roles are seeded idempotently at startup: `VIEWER` has read-only operational permissions and no live-camera permission; `SUPERVISOR` can view cameras, acknowledge/assign events, and review/manage response workflows. Neither role can administer accounts or edit plant configuration. Existing role rows are preserved and are not silently rewritten by reseeding.

Run the static dashboard from a second project-root terminal with `python -m http.server 8001 --directory frontend`; open `http://127.0.0.1:8001`.

Camera API responses redact URL user information and query parameters. Credential- or query-bearing camera URLs require `CAMERA_URL_ENCRYPTION_KEY`; losing that key makes stored values unreadable. Do not commit `.env`, credentials, or camera URLs containing secrets.

### Camera URL Encryption

Credential- or query-bearing camera URLs require `CAMERA_URL_ENCRYPTION_KEY`. Generate a Fernet key locally and store it only in a secret manager or `.env`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the resulting value as `CAMERA_URL_ENCRYPTION_KEY` before creating such cameras. Losing or changing this key makes encrypted camera sources unreadable; perform key rotation with a separately planned, backed-up migration. Existing legacy camera URLs stored before encryption may require a controlled re-encryption procedure; do not assume old rows were encrypted retroactively.
