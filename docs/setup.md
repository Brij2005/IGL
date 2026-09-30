# Setup

## Development

From the project root, install the pinned dependencies, copy `.env.example` to `.env`, then run the migration and API:

```powershell
python -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
alembic -c backend/alembic.ini upgrade head
python scripts/bootstrap_admin.py
uvicorn backend.app.main:app --reload
```

The API refuses to start unless the database revision matches the Alembic head. Schema is owned exclusively by the migrations; `init_db()` no longer creates tables and raises instead.

The one-time bootstrap seeds the fixed platform roles and creates the first administrator only when the users table is empty. Startup never creates an administrator.

Two bootstrap modes are available, neither of which has a default credential:

- Interactive (default): prompts for a new username, email, full name, and password of at least 12 characters, without echoing it.
- Non-interactive: set `BOOTSTRAP_ADMIN_ENABLED=true` plus every `BOOTSTRAP_ADMIN_*` value, then run `python scripts/bootstrap_admin.py --configured`. Remove those values afterwards.

Either way, bootstrap refuses to run once any user exists, rejects a password that bcrypt cannot represent, and never logs a credential.

Login throttling is configurable through `LOGIN_RATE_LIMIT_ATTEMPTS` and `LOGIN_RATE_LIMIT_WINDOW_SECONDS`. The current limiter is in-process; multi-worker deployments need a shared rate-limit store.

The default SQLite location resolves relative to the project root. Migrations add schema to an existing database; do not delete, recreate, or reset a database as a setup step.

## Tests

```powershell
python -m pytest backend/tests -q
```

`backend/tests/conftest.py` creates a temporary file database, migrates it with the real Alembic chain, and points the application at it before any application module is imported. The suite therefore never reads, writes, or drops your configured database.

## Model Configuration

Set `MODEL_WEIGHTS_PATH` to an existing local model file. `MODEL_NAME`, `MODEL_VERSION`, and `MODEL_CONFIDENCE_THRESHOLD` provide traceable metadata. The service does not download weights. An absent file yields `MODEL_NOT_CONFIGURED`; an invalid configured path yields `MODEL_INVALID_WEIGHTS`.

## Production Safeguards

Set `ENVIRONMENT=production`, provide a unique `SECRET_KEY` with at least 32 characters through environment configuration, and set explicit `BACKEND_CORS_ORIGINS`. Production startup rejects the development key and wildcard CORS. Never commit `.env`, credentials, or camera URLs containing secrets. The `.env.example` key is development-only.

Camera API responses remove RTSP URL user information and redact common credential query parameters. Restrict access to camera configuration because the application still needs the original stream URL to connect.

### Camera URL Encryption

Credential- or query-bearing camera URLs require `CAMERA_URL_ENCRYPTION_KEY`. Generate a Fernet key locally and store it only in a secret manager or `.env`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the resulting value as `CAMERA_URL_ENCRYPTION_KEY` before creating such cameras. Losing or changing this key makes encrypted camera sources unreadable; perform key rotation with a separately planned, backed-up migration. Existing legacy camera URLs stored before encryption may require a controlled re-encryption procedure; do not assume old rows were encrypted retroactively.