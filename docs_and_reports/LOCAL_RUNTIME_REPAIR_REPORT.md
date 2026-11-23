# IGL VisionGuard — Local Runtime Diagnosis and Repair Report

Date: 2026-11-23
Project root: `C:\Users\Krishna Sharma\Desktop\IGL`

---

## 1. Exact root cause of the failure

The application was **not broken**. It was already running correctly. The failures came from the
launch environment, not from project code. There were two independent causes:

### Cause A — Port 8000 was already occupied by a healthy IGL backend (WinError 10048)

A previous IGL backend instance was still running and holding the port:

```
port 8000 -> PID 6640  python  (started 2026-11-23 09:24:33)
command: backend\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Process tree (a single logical server, not a duplicate):

```
PID 9272  (background shell)
  └── PID 20752  python -m uvicorn   (uvicorn parent)
        └── PID 6640  python         (serving process, owns 127.0.0.1:8000)
```

Starting a second uvicorn therefore failed with
`[WinError 10048] only one usage of each socket address`. **The application itself was healthy.**

Verification that PID 6640 was genuinely healthy (not merely alive):

| Probe | Result |
|---|---|
| `GET /api/system/health` | **HTTP 200**, `{"status":"OK","database":{"connected":true}}` |
| Structured JSON startup log | `startup_complete ... environment=development auth_enabled=false` |
| Source freshness | newest backend source `api_routes.py` 09:02:38 < server start 09:24:33 → running current code |
| 30-endpoint sweep | all HTTP 200 |

Because it was healthy, it was **reused, not killed** (per the safety rules).

### Cause B — The global Python interpreter has an incompatible FastAPI/Starlette pair

Running with global Python produced:

```
TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'
```

Confirmed by inspecting both environments:

| Environment | Python | FastAPI | Starlette | `starlette.Router` accepts `on_startup` |
|---|---|---|---|---|
| **Project venv** (correct) | 3.13 | 0.165.0 | 1.10.0 | n/a — FastAPI's `APIRouter` accepts it; app imports and runs |
| **Global** (incorrect) | 3.13.7 | different build | different build | incompatible → raises the `TypeError` |

`backend\.venv\Scripts\python.exe -m pip check` → **`No broken requirements found`**, so the venv is
internally consistent. The global environment is a separate, mismatched installation and must not be
used for this project. **No dependency was changed** — none needed changing.

### Summary

| Symptom | Real cause | Code defect? |
|---|---|---|
| `WinError 10048` on startup | Healthy IGL backend already listening on 8000 | No |
| `TypeError: Router.__init__() ... on_startup` | Global Python has incompatible FastAPI/Starlette | No (wrong interpreter) |
| Broken/missing favicon in browser | `index.html` referenced `/favicon.svg`, asset absent | **Yes — fixed** |

---

## 2. Files changed and reason for each change

| File | Change | Reason |
|---|---|---|
| `frontend/public/favicon.svg` | **Added** (new SVG icon) | `frontend/index.html:6` declares `<link rel="icon" type="image/svg+xml" href="/favicon.svg" />`, but no such file existed in `frontend/public/`. Vite's SPA fallback therefore returned `index.html` (HTML, 1078 bytes) for `/favicon.svg`, so the browser could not load the icon. Verified before: `Content-Type` was HTML; after: `image/svg+xml`, 553 bytes. |

No other source file was modified. No feature was removed, no module disabled, no database touched.

Committed as `1d018ae` — `fix(frontend): add missing favicon.svg referenced by index.html`.

---

## 3. Dependency changes

**None.**

Audited in the project venv only (`backend\.venv\Scripts\python.exe`):

| Package | Installed | Declared in `requirements.txt` | Compatible |
|---|---|---|---|
| fastapi | 0.165.0 | `>=0.110.0,<1.0.0` | yes |
| starlette | 1.10.0 | (transitive via fastapi) | yes — `pip check` clean |
| uvicorn | 0.54.0 | `>=0.28.0` | yes |
| pydantic | 2.14.4 | `>=2.6.0` | yes |
| pydantic-core | 2.50.5 | (transitive) | yes |
| anyio | 4.16.0 | (transitive) | yes |
| httpx | 0.28.1 | `>=0.27.0` | yes |
| ultralytics | 8.4.216 | `>=8.1.0` | yes |
| opencv-python | 5.1.0.95 | `>=4.9.0` | yes |
| numpy | 2.5.5 | `>=1.26.0` | yes |
| python-multipart | 0.0.34 | `>=0.0.9` | yes |
| cryptography | 50.0.2 | `>=42.0.0` | yes |

`pip check` → `No broken requirements found`. Nothing was upgraded or downgraded.

---

## 4. Backend status and URL

| Item | Value |
|---|---|
| Status | **RUNNING** |
| URL | `http://127.0.0.1:8000` |
| Health | `GET /api/system/health` → **HTTP 200**, `status: OK`, `database.connected: true` |
| Swagger UI | `http://127.0.0.1:8000/docs` → **HTTP 200** |
| OpenAPI spec | `http://127.0.0.1:8000/openapi.json` → **HTTP 200**, 80+ documented paths |
| Serving PID | 6640 (parent 20752) |
| Startup/shutdown lifecycle | Verified — structured JSON `startup_complete` logged; shutdown hook stops camera workers |
| Auth configuration | `ENABLE_AUTH=false` (development mode, as configured in `backend/.env`) |

## 5. Frontend status and URL

| Item | Value |
|---|---|
| Status | **RUNNING** |
| URL | `http://localhost:3000` |
| Response | **HTTP 200**, valid Vite SPA shell (`<div id="root"></div>` + `/src/main.jsx`) |
| Dev server | Vite 5.4.21, ready in 653 ms, no errors in logs |
| Module transform | `/src/main.jsx` → HTTP 200, transformed with React refresh (no build errors) |
| Static assets | `/favicon.svg` → 200 `image/svg+xml`; `/plant_schematic.jpg` → 200 `image/jpeg` |
| Node/npm | Node 20-compatible Vite 5 toolchain already installed; `node_modules` present |

## 6. Health-check HTTP status

| Check | Status |
|---|---|
| `GET http://127.0.0.1:8000/api/system/health` | **200** |
| `GET http://127.0.0.1:8000/api/monitoring/health` | **200** (database UP) |
| `GET http://127.0.0.1:8000/docs` | **200** |
| `GET http://localhost:3000/` | **200** |
| `GET http://127.0.0.1:8000/api/dashboard/stats` (with `Origin`) | **200** + `Access-Control-Allow-Origin: http://localhost:3000` |

## 7. Database integrity result

| Check | Result |
|---|---|
| Path | `backend/igl_safety.db` (48.4 MB, **not recreated or overwritten**) |
| `PRAGMA integrity_check` | **ok** |
| Migrations applied | **1, 2, 3, 4, 5** |
| Tables | 36 |
| `PRAGMA foreign_key_check` | **no violations** |
| Models | `model-yolov8n` AVAILABLE, `model-water-leakage` AVAILABLE (v3), `model-pipe-corrosion` NOT_TRAINED, `model-thermal-person` NOT_TRAINED |
| Users | `admin` (admin) preserved |
| Records | cameras 0, detection_events 0, alerts 0, evidence_files 0, access_records 0, asset_register 0 |

Existing data, models and evidence are intact. No records were added or removed.

## 8. Tests executed and pass/fail counts

| Test / check | Result |
|---|---|
| Backend suite `pytest -q` | **131 passed, 0 failed** (5 pre-existing FastAPI deprecation warnings) |
| Frontend production build `npm run build` | **PASSED** — 2780 modules, ~23 s |
| Live E2E verification against running services | **60+ checks passed, 0 failed** |
| Endpoint sweep (30 SPA endpoints) | **30/30 HTTP 200** |
| Dependency health `pip check` | **No broken requirements** |
| Database integrity | **ok**, 0 FK violations |
| CORS preflight + credentialed GET | **PASS** (`ACAO: http://localhost:3000`) |

### E2E coverage actually verified (not inferred from startup logs)

1. Backend process running — PASS
2. Health endpoint HTTP 200 — PASS
3. Swagger docs + OpenAPI reachable — PASS
4. Frontend HTTP 200 with valid SPA shell — PASS
5. Frontend↔backend communication incl. CORS preflight — PASS
6. Auth per configuration (`/auth/me` → admin in dev mode) — PASS
7. Dashboard loads real values — PASS
8. All 30 existing API routes respond — PASS
9. Database intact — PASS
10. Existing modules available — PASS
11. No fabricated data — PASS (0 cameras, 0 events, 0 alerts, all counters 0)

Module-level runtime checks:

| Module | Verified |
|---|---|
| Water Leakage | `POST /api/models/model-water-leakage/detect` → `SUCCESS`; `/api/incidents/leakage` → 200 |
| Pipeline Inspection | `/api/plant-locations` and `/api/pipelines` return seeded data (200) |
| Smart Entry & Exit | `/api/security/dashboard` → 200 with `pending_verification` |
| Thermal ingestion | RGB upload correctly **rejected 422**; `/api/security/thermal/status` → 200, device not connected |
| Camera monitoring | `/api/cameras` → `[]`; `/api/security/workers` → no workers (honest) |
| Reports & alerts | `/api/reports` → 200 list; `/api/alerts` → 200 list |
| Hardware | 8 categories, **all NOT connected** (no fakes) |

> No hardware functionality is claimed: no cameras, thermal devices, RFID readers or access-control
> hardware are connected.

## 9. Remaining errors or limitations

**No blocking errors remain.** Both services run and communicate correctly.

Non-blocking notes:

1. **Global Python must not be used.** `python -m uvicorn ...` resolves to global Python 3.13.7 whose
   FastAPI/Starlette pair is incompatible (`Router.__init__() got an unexpected keyword argument
   'on_startup'`). Always use `backend\.venv\Scripts\python.exe`.
2. **Only one backend instance can bind port 8000.** A second launch will fail with WinError 10048 by
   design. Check with `Get-NetTCPConnection -LocalPort 8000 -State Listen` before starting.
3. **Development runs with `ENABLE_AUTH=false`.** This is the repository's dev configuration; login and
   RBAC are verified separately with auth enabled (131-test suite includes 19 auth/RBAC assertions).
   Production requires `ENABLE_AUTH=true` plus strong secrets (enforced by the startup guard).
4. **Corrosion and thermal models are `NOT_TRAINED`** — genuine labelled datasets are unavailable and
   there is no GPU. They correctly report `MODEL_UNAVAILABLE` and produce no detections.
5. **Docker is not installed** on this host, so container startup was not verified (out of scope here;
   local run is what was requested and verified).
6. **The Roboflow API key in `backend/.env` should still be rotated** — it was echoed in a local
   diagnostic output in an earlier session. It is not committed to Git.

## 10. Exact commands to run the project next time

Run from the project root `C:\Users\Krishna Sharma\Desktop\IGL`.

### Check whether a backend is already running (do this first)

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

If a PID is returned, confirm it is IGL before doing anything:

```powershell
Get-CimInstance Win32_Process -Filter "ProcessId=<PID>" | Select-Object ProcessId, CommandLine
```

If it is the IGL backend and healthy, just open http://localhost:3000 — nothing to start.

### Start the backend (only if port 8000 is free)

```powershell
cd "C:\Users\Krishna Sharma\Desktop\IGL\backend"
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### Start the frontend (second terminal)

```powershell
cd "C:\Users\Krishna Sharma\Desktop\IGL\frontend"
npm run dev
```

### URLs

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Backend API | http://127.0.0.1:8000/api |
| Swagger docs | http://127.0.0.1:8000/docs |
| Health | http://127.0.0.1:8000/api/system/health |

### Stop a confirmed IGL backend gracefully

```powershell
Stop-Process -Id <IGL_PID>            # only after confirming it is the IGL uvicorn process
```

### Run tests / build

```powershell
cd "C:\Users\Krishna Sharma\Desktop\IGL\backend"
.\.venv\Scripts\python.exe -m pytest -q

cd "C:\Users\Krishna Sharma\Desktop\IGL\frontend"
npm run build
```

### Do NOT use

```powershell
python -m uvicorn ...   # wrong: global Python, incompatible FastAPI/Starlette
```

---

## Final statement

The IGL VisionGuard frontend and backend are **running and verified communicating**:

- Backend `http://127.0.0.1:8000` — HTTP 200, database connected, Swagger reachable, 30/30 API routes OK.
- Frontend `http://localhost:3000` — HTTP 200, SPA shell and module graph transform cleanly.
- CORS verified in both preflight and credentialed requests.
- 131 backend tests pass; frontend production build passes; database integrity `ok`.

The reported startup failures were caused by (A) an already-running healthy IGL backend holding port
8000 and (B) use of the incompatible global Python interpreter — neither was an application defect.
The one genuine defect found (missing `favicon.svg`) was fixed and pushed (`1d018ae`).

This is **local development verification**. It is not a production-readiness or hardware-validation claim.