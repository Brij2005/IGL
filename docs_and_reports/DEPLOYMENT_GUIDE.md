# IGL VisionGuard — Deployment Guide

This document covers running IGL VisionGuard in development, staging and
production. It reflects the configuration that is actually implemented in the
codebase; nothing here assumes untested features.

---

## 1. Configuration Model

Configuration is environment-driven (`backend/config.py`) and split into two
templates:

| Template | Purpose | Auth |
|---|---|---|
| `backend/.env.example` | Local development | `ENABLE_AUTH=false` (dashboard opens directly) |
| `backend/.env.production.example` | Staging / production | `ENABLE_AUTH=true` (mandatory) |

`ENVIRONMENT` selects the mode. In production the application **refuses to
start** when any of these unsafe conditions is present
(`config.validate_production_config`, called from the FastAPI startup hook):

- `ENABLE_AUTH` is not `true`
- `SECRET_KEY` is the development default or shorter than 32 characters
- `ADMIN_PASSWORD` is the development default or shorter than 12 characters

This guarantees authentication can never be silently disabled in production.

---

## 2. Development

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # ENABLE_AUTH=false
python -m uvicorn main:app --reload --port 8000
```

```bash
cd frontend
npm install
npm run dev                       # Vite dev server
```

Behaviour in development:

- Authentication is disabled, so the dashboard opens without a login.
- Camera sources are configured in the UI; only reachable sources report ONLINE.
- Recorded-video analysis works end-to-end with the COCO model.
- Thermal inference reports `MODEL_UNAVAILABLE` (no trained thermal model).

---

## 3. Staging

Goal: production-like configuration on a non-production host.

1. Copy `backend/.env.production.example` to `backend/.env` and fill real values.
2. Set `ENVIRONMENT=production` and `ENABLE_AUTH=true`.
3. Generate a strong secret:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`
4. Set a strong `ADMIN_PASSWORD` (the seed admin is created on first start).
5. Start with the real database path and persistent evidence/reports directories.
6. Verify:
   - Login required for all `/api/*` endpoints (static assets stay public).
   - Admin / Operator / Viewer role separation behaves as documented.
   - Logout revokes the presented token.
   - Camera worker starts/stops and reports honest status.
   - Reports and evidence are retrievable and access-controlled.

---

## 4. Production

### 4.1 Container build

```bash
docker compose up -d --build
```

`docker-compose.yml` requires a strong `SECRET_KEY` and `ADMIN_PASSWORD`
(it fails fast if they are unset) and defaults `ENABLE_AUTH` to `true`.

> Note: container build could not be verified in the development environment
> because Docker is not installed there. The Dockerfile and compose file were
> validated statically only.

### 4.2 Required controls

| Control | Implementation |
|---|---|
| Authentication | Mandatory in production (startup refuses otherwise) |
| HTTPS | Terminate TLS at a reverse proxy in front of the app |
| Secrets | Environment variables only; `.env` is gitignored and excluded from the image via `.dockerignore` |
| Role-based access | `require_roles(...)` on all mutating endpoints; enforced server-side |
| Evidence protection | `/api/evidence/*` is behind the API auth middleware |
| Audit logging | Login, logout, user/camera/model/incident changes are recorded in `audit_logs` |
| Rate limiting | `rate_limit` dependency available for sensitive endpoints |
| Camera streams | `?token=` query auth supported for `<img>`/`<video>` tags |
| Worker health | `GET /api/security/workers` reports real worker state |
| Graceful shutdown | FastAPI shutdown hook stops all camera workers |
| Persistence | `/data` volume holds the DB, evidence and reports; `/app/models` holds weights |
| Logging | Docker json-file driver with rotation (10 MB × 5) |
| Health check | Compose healthcheck hits `/api/system/health` |

### 4.3 Post-deployment verification

1. `GET /api/cameras` without a token → `401`.
2. Login → token → `GET /api/cameras` → `200`.
3. Viewer token → `POST /api/cameras` → `403`.
4. Logout → previous token → `401`.
5. `GET /api/security/hardware/status` → every non-camera device `NOT_CONNECTED`.
6. `GET /api/security/workers` → empty until a camera is started.

---

## 5. Rollback

- Application rollback: redeploy the previous image tag; the database is
  forward-compatible because migrations are additive only.
- Model rollback: `POST /api/models/{model_id}/activate-version` with the
  target `version` repoints `weights_path` and `active_version` to an already
  registered version and reloads the model.
- Database: keep volume snapshots; no destructive migration is used by this project.

---

## 6. Known Limitations

- Thermal person detection is untrained (`NOT_TRAINED`); thermal inference
  returns `MODEL_UNAVAILABLE` by design.
- Temperature readings require a calibrated source (`calibrated=true`), otherwise
  the API rejects them.
- Hardware adapters for RFID, access control, turnstile, alarm, central server
  and storage report `NOT_CONNECTED` until a real transport is implemented and
  a device is configured.
- ONVIF device discovery is not implemented (pre-existing limitation).
- Container build is unverified in this environment (no Docker).