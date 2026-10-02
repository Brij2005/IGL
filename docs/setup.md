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

This local build has no authentication. Keep it bound to localhost; do not expose it to an untrusted network or assume an upstream proxy identity is enforced by this API.

The default SQLite location resolves relative to the project root. Migrations add schema to an existing database; do not delete, recreate, or reset a database as a setup step.

## Tests

```powershell
python -m pytest backend/tests -q
```

`backend/tests/conftest.py` creates a temporary file database, migrates it with the real Alembic chain, and points the application at it before any application module is imported. The suite therefore never reads, writes, or drops your configured database.

## Model Configuration

Set `MODEL_WEIGHTS_PATH` to an existing local model file. `MODEL_NAME`, `MODEL_VERSION`, and `MODEL_CONFIDENCE_THRESHOLD` provide traceable metadata. The service does not download weights. An absent file yields `MODEL_NOT_CONFIGURED`; an invalid configured path yields `MODEL_INVALID_WEIGHTS`.

## Deployment Boundary

This build has no authentication. `ENVIRONMENT=production` or `staging` rejects `ALLOW_ANONYMOUS_ACCESS=true`; setting it false makes protected routes return 401 because no authentication implementation exists. This is not a production deployment configuration. Keep the demo bound to localhost. Before any shared deployment, implement and review authentication/authorization, select a production database, configure TLS/reverse proxy/network controls, and perform security review. Wildcard CORS is rejected; use exact origins.

Run the static dashboard from a second project-root terminal with `python -m http.server 8001 --directory frontend`; open `http://127.0.0.1:8001`.

Camera API responses redact URL user information and query parameters. Credential- or query-bearing camera URLs require `CAMERA_URL_ENCRYPTION_KEY`; losing that key makes stored values unreadable. Do not commit `.env`, credentials, or camera URLs containing secrets.

### Camera URL Encryption

Credential- or query-bearing camera URLs require `CAMERA_URL_ENCRYPTION_KEY`. Generate a Fernet key locally and store it only in a secret manager or `.env`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the resulting value as `CAMERA_URL_ENCRYPTION_KEY` before creating such cameras. Losing or changing this key makes encrypted camera sources unreadable; perform key rotation with a separately planned, backed-up migration. Existing legacy camera URLs stored before encryption may require a controlled re-encryption procedure; do not assume old rows were encrypted retroactively.