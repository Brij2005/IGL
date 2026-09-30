# Setup

## Development

From the project root, install the pinned dependencies, copy `.env.example` to `.env`, then run the migration and API:

```powershell
python -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
alembic -c backend/alembic.ini upgrade head
uvicorn backend.app.main:app --reload
```

The default SQLite location resolves relative to the project root. The migration adds schema to a new database; do not delete, recreate, or reset an existing database as a setup step.

## Model Configuration

Set `MODEL_WEIGHTS_PATH` to an existing local model file. `MODEL_NAME`, `MODEL_VERSION`, and `MODEL_CONFIDENCE_THRESHOLD` provide traceable metadata. The service does not download weights. An absent file yields `MODEL_NOT_CONFIGURED`; an invalid configured path yields `MODEL_INVALID_WEIGHTS`.

## Production Safeguards

Set `ENVIRONMENT=production`, provide a unique `SECRET_KEY` with at least 32 characters through environment configuration, and set explicit `BACKEND_CORS_ORIGINS`. Production startup rejects the development key and wildcard CORS. Never commit `.env`, credentials, or camera URLs containing secrets. The `.env.example` key is development-only.

Camera API responses remove RTSP URL user information and redact common credential query parameters. Restrict access to camera configuration because the application still needs the original stream URL to connect.