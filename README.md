# IGL VisionGuard AI

Industrial AI monitoring software for camera management, water pipeline leakage detection, safety events, evidence, alerts, reports and auditability.

This repository extends the existing IGL VisionGuard application. The original FastAPI backend, SQLite schema and React frontend are preserved. New operations modules and pages were added rather than replacing the application.

## Current Status

- Backend: FastAPI + SQLite + OpenCV + Ultralytics YOLOv8.
- Frontend: React + Vite + Tailwind.
- Authentication: optional backend token/RBAC support; disabled by default for the local dashboard.
- Camera sources: RTSP, ONVIF/RTSP, webcam, file, image, public HTTP.
- Camera preview: backend-decoded MJPEG stream (browser-safe).
- Camera credentials: encrypted at rest and never returned to the frontend.
- Leakage model: **AVAILABLE**, active version **3** (`model-water-leakage/v3/best.pt`), trained on a combined genuine multi-source dataset. Versions 1 and 2 are preserved for rollback.
- Tests: 23 backend tests passing; frontend production build passing.
- Backup: `.dist/backup/20261121-182642/`.

See [PROJECT_AUDIT.md](PROJECT_AUDIT.md) for the detailed audit, test evidence, limitations and acceptance checklist.

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
python -m uvicorn main:app --reload --port 8000
```

Create a `.env` from `backend/.env.example` and set at least:

```env
ENABLE_AUTH=false
SECRET_KEY=<long-random-secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<strong-password>
DATABASE_PATH=igl_safety.db
```

Authentication is disabled by default so the app opens directly on the dashboard (the login and user-registration pages have been removed). The first startup still creates the administrator account and initializes/migrates SQLite.

### Frontend

```bash
cd frontend
npm install
copy .env.example .env
npm run dev
```

Set `VITE_API_BASE` to your backend URL, for example:

```env
VITE_API_BASE=http://127.0.0.1:8000/api
```

Build:

```bash
npm run build
```

## Camera Installation Workflow

1. Open **Camera Management** (no sign-in required).
2. Open **Camera Management**.
3. Enter the camera name and authorized RTSP/ONVIF URL.
4. Enter username/password if required.
5. Register the camera.
6. Click **Test** and verify a real preview.
7. Open **Camera Angle & Zones**.
8. Set resolution/FPS and start the camera.
9. Adjust the physical camera angle at installation.
10. Refresh the frame preview.
11. Draw the pipeline monitoring polygon.
12. Select models, save the zone, and map the camera to a plant location/pipeline.
13. Start monitoring.

No new application development is required for ordinary camera onboarding.

## Water Leakage AI

Required classes:

1. Water Dripping
2. Water Spraying
3. Visible Water Flow
4. Pipe Joint Leakage
5. Water Accumulation
6. Normal/Dry Pipeline

The leakage model is trained and registered as active version **3**. It covers all six configured classes, but Water Dripping and Water Spraying remain weak. This is a multi-class baseline, not a production accuracy guarantee. Versions 1 and 2 remain preserved for rollback.

### Version 3 trained model results

- Active model: `model-water-leakage`, version `3`, weights `model-water-leakage/v3/best.pt`.
- Combined dataset: 12,685 images, 12,685 labels, 110 duplicate images removed, 9 missing-label images skipped, zero malformed annotations.
- Class distribution:
  - Water Dripping: 255 boxes
  - Water Spraying: 582 boxes
  - Visible Water Flow: 2,025 boxes
  - Pipe Joint Leakage: 4,236 boxes
  - Water Accumulation: 6,468 boxes
  - Normal/Dry Pipeline: 2,574 boxes
- Source-aware split: 7,931 train / 2,377 validation / 2,377 independent test.
- Balanced training set: 8,963 train images after deterministic oversampling of Water Dripping and Water Spraying; validation and test unchanged.
- Training: YOLOv8n, 4 epochs, 192px, batch 32, CPU (Intel i5-1035G1, no CUDA), 3,182.8 seconds.
- Validation: precision 0.334, recall 0.343, mAP50 0.242, mAP50-95 0.144.
- Independent test: precision 0.476, recall 0.269, mAP50 0.185, mAP50-95 0.127.
- Test mAP50 by class:
  - Water Dripping: 0.0669
  - Water Spraying: 0.0226
  - Visible Water Flow: 0.0850
  - Pipe Joint Leakage: 0.2941
  - Water Accumulation: 0.5282
  - Normal/Dry Pipeline: 0.1151
- CPU inference speed during evaluation: 14.2 ms per image.
- Model size: 6,453,994 bytes.
- Model card: `docs_and_reports/WATER_LEAKAGE_MODEL_CARD.json`.
- Detailed evaluation: `docs_and_reports/evaluations/v3/evaluation.md`.
- Model comparison: `docs_and_reports/LEAKAGE_MODEL_COMPARISON.md`.

### V1 / V2 / V3 comparison on the same independent test split

| Version | Precision | Recall | mAP50 | mAP50-95 | Notes |
|---|---:|---:|---:|---:|---|
| V1 | 0.0628 | 0.0529 | 0.0419 | 0.0267 | Single-class Water Accumulation model |
| V2 | 0.2791 | 0.2197 | 0.1377 | 0.0874 | Multi-class; no Water Dripping or Water Spraying performance |
| V3 | 0.4760 | 0.2689 | 0.1853 | 0.1271 | Best aggregate and class coverage; active version |

V3 has the best aggregate precision, recall and mAP, and is the only version with non-zero Water Dripping and Water Spraying mAP. It is still not production-grade for those weak classes. V1 and V2 are preserved for rollback.

### Genuine video testing

Two genuine public water-leak videos from `freedomwebtech/yolo11-water-leak` were used for inference testing (not factory CCTV):

- `water_leak_5.mp4`: 300 frames, 0 detections at confidence 0.25; 46 `Water Spraying` predictions at confidence 0.10.
- `water_leak_6.mp4`: 173 frames, 6 `Water Accumulation` detections at confidence 0.25; 26 predictions at confidence 0.10 (20 `Water Accumulation`, 6 `Water Spraying`).

After the event-deduplication fix, the same `water_leak_5.mp4` event-path test produced 4 stored detection events, 4 evidence files and 2 alerts. Those temporary evaluation records were removed from the operational database after verification. This is not equivalent to factory CCTV validation.

### Dataset sources researched

Genuine public datasets used for version 3 (all CC BY 4.0):

- `water-leakage-detection/water-leakage` v4: `Standing Water` -> `Water Accumulation`.
- `kai-bsnlf/water-leakage-bnrzx` v1: `liquid_drip_leakage` -> `Water Dripping`; `liquid_jet_leakage` -> `Water Spraying`. Fire, gas and smoke annotations were dropped.
- `jaishreeram/pipeline_leakage1` v1: `leak` -> `Pipe Joint Leakage`; `water` -> `Visible Water Flow`; `no leak` -> `Normal/Dry Pipeline`. Crack annotations were dropped.
- `onekode3/pipeline_leak1` v1: `water leak` -> `Pipe Joint Leakage`; `no leak` -> `Normal/Dry Pipeline`. Gas-leak annotations were dropped.
- `projet-5cii8/pipe-leak-u31py` v1: `water` -> `Visible Water Flow`. Gas and crack annotations were dropped.

`leak-adhw8/water-leak-wgdaq` v3 was downloaded and inspected but excluded because its exported class name is only `0` and could not be semantically verified. A 56-image `kyle-scally-4ixoi/pipeline-cctv-defect` set was inspected but not used as a reliable Pipe Burst source because it has only 19 `Broken pipe` boxes. No unrelated classes were relabeled into leakage categories.

Exporting a public Universe dataset requires a Roboflow API key. Create `backend/.env` from `backend/.env.example`, set `ROBOFLOW_API_KEY=<your-key>` in that file, and keep it local. `backend/.env` is gitignored and must never be committed. The download script loads `backend/.env` automatically; you can also pass `--api-key` or set the variable in the process environment. Never put the key in frontend code, API responses, or logs. The downloaded datasets and generated weights are gitignored local artifacts and are not bundled in the repository.

### Download, validate, train and evaluate

```bash
# 1. Download a genuine YOLO-format export (requires ROBOFLOW_API_KEY)
python backend/download_leakage_dataset.py \
  --workspace <workspace> --project <project> --version <version> \
  --format yolov8 --destination backend/data/leakage

# 2. Validate annotations
python backend/leakage_training.py validate --dataset-root backend/data/leakage

# 3. Clean, deduplicate and split each source into train/val/test
python backend/leakage_training.py prepare --dataset-root backend/data/leakage
python backend/leakage_training.py prepare --dataset-root backend/data/leakage_multisource/kai-water-leakage
python backend/leakage_training.py prepare --dataset-root backend/data/leakage_multisource/jaishreeram-pipeline-leakage1
python backend/leakage_training.py prepare --dataset-root backend/data/leakage_multisource/onekode3-pipeline-leak1

# 4. Combine sources with explicit class mapping and source-group-aware splits
python backend/leakage_training.py combine --config backend/data/leakage_multisource/combine_config.json --output-root backend/data/leakage_multiclass --val-ratio 0.15 --test-ratio 0.15 --seed 42

# 5. Validate the combined dataset
python backend/leakage_training.py validate --dataset-root backend/data/leakage_multiclass

# 6. Rebalance weak classes in the training split only
python backend/leakage_training.py oversample --dataset-root backend/data/leakage_multiclass --output-root backend/data/leakage_multiclass_balanced --classes 'Water Dripping,Water Spraying' --factor 4 --seed 42

# 7. Train version 3
python backend/leakage_training.py train --dataset-root backend/data/leakage_multiclass_balanced --prepared-root backend/data/leakage_multiclass_balanced --model-id model-water-leakage --version 3 --epochs 4 --imgsz 192 --batch 32

# 8. Detailed independent test evaluation
python backend/evaluate_leakage.py --weights backend/models/model-water-leakage/v3/best.pt --dataset-root backend/data/leakage_multiclass_balanced --split test --imgsz 192 --conf 0.25 --output-dir backend/docs_and_reports/evaluations/v3

# 9. Reload the active model
curl -X POST http://127.0.0.1:8000/api/models/model-water-leakage/reload

# 10. Optional: run recorded-video inference
python backend/leakage_training.py video --weights backend/models/model-water-leakage/v3/best.pt --video /path/to/recorded.mp4
```

The event engine creates leakage incidents as `POSSIBLE` or `SUSPECTED`. Only an authorized human review can mark an incident `CONFIRMED`. Water accumulation alone is never automatically promoted to a confirmed pipe leakage.

## Event Engine

The central event engine:

- accepts genuine model predictions
- applies per-camera thresholds and temporal verification
- tracks detections across frames
- suppresses duplicate events/alerts
- captures evidence
- stores detection events, incidents and alerts
- associates events with camera, plant location, pipeline and zone
- supports human review and false-positive/negative recording

## API Surface

New operations APIs are mounted under `/api`:

- `POST /api/auth/login`, `GET /api/auth/me` (only active when `ENABLE_AUTH=true`; login page removed from the UI)
- `GET/POST/DELETE /api/cameras`, `PUT /api/cameras/{id}/config`
- `POST /api/cameras/{id}/start|stop|test`, `GET /api/cameras/{id}/health`
- `GET /api/cameras/{id}/frame.jpg`, `GET /api/cameras/{id}/stream.mjpg`
- `GET/POST/DELETE /api/zones`
- `GET/POST /api/plant-locations`
- `GET/POST /api/pipelines`
- `GET /api/models`, `GET /api/models/health`, `GET /api/models/{id}/versions`, `GET /api/models/{id}/evaluation`, `POST /api/models/{id}/detect`, `POST /api/models/{id}/reload`, `POST /api/models/{id}/evaluate-video`
- `GET /api/datasets`, `POST /api/datasets/validate`, `POST /api/datasets/prepare`
- `GET /api/events`, `POST /api/events/process`
- `GET /api/incidents/leakage`, `PUT /api/incidents/leakage/{id}/review`
- `GET /api/incidents/safety`, `PUT /api/incidents/safety/{id}/review`
- `GET /api/evidence`, `GET /api/evidence/{id}`
- `GET /api/notifications/status`, `POST /api/notifications/test`
- `POST /api/reports`, `GET /api/reports`, `GET /api/reports/{id}`, `GET /api/reports/{id}/download`
- `GET /api/audit-logs`
- `GET/POST/PUT/DELETE /api/users`
- `GET/PUT /api/settings`
- `GET /api/system/health`

Existing endpoints such as `/api/cameras`, `/api/alerts`, `/api/observations`, `/api/restricted-zones`, `/api/dashboard/stats`, `/api/yolo/detect` and personnel endpoints remain available.

## Notifications

Implemented providers:

- in-app
- email via SMTP
- SMS via configurable provider
- Telegram
- WhatsApp via the existing backend service

Default WhatsApp mode is `mock` and is explicitly labeled as mock. Unconfigured providers are reported as `NOT_CONFIGURED`; the system does not report successful delivery unless the provider accepts the message.

Provider configuration uses environment variables in `backend/.env.example`.

## Deployment

### Docker

```bash
docker compose up --build
```

The container serves the built frontend and the FastAPI API on port `8000`.

Persistent volumes:

- `igl-data` — database, evidence and reports
- `igl-models` — model weights

### Production checklist

- Set a strong `SECRET_KEY`.
- Change `ADMIN_PASSWORD`.
- Set `ENABLE_AUTH=true` only if you re-enable API token authentication (the login page has been removed; use `POST /api/auth/login` directly).
- Configure `ALLOWED_ORIGINS` for the deployed frontend origin.
- Configure SMTP/SMS/Telegram/WhatsApp credentials if required.
- Mount persistent storage for `DATABASE_PATH`, `EVIDENCE_DIR`, `REPORTS_DIR` and `MODEL_DIR`.
- Ensure the backend host can reach the camera network and RTSP ports.
- Do not attempt to process private CCTV in a static frontend deployment.
- Retrain and re-evaluate the leakage model before relying on new leakage classes or new camera angles.
- Test camera connectivity and evidence retention before production use.

## Testing

```bash
cd backend
.venv\Scripts\python.exe -m pytest -q
```

```bash
cd frontend
npm run build
```

Backend tests cover authentication, camera lifecycle, zones, real YOLO inference, leakage model unavailability before registration, trained leakage model inference after registration, event pipeline, notifications, reports, evidence, auth-enabled API protection, dataset preparation/deduplication/class remapping, polygon annotation conversion, unmapped-class dropping, model reload, and video-evaluation endpoint behavior.

## Known Limitations

- No physical factory CCTV camera is available.
- No genuine recorded factory footage is bundled.
- Version 3 covers all six classes, but Water Dripping and Water Spraying have very low test mAP; they should not be treated as reliable production detectors yet.
- Training used 192px images for 4 epochs on CPU; this is a multi-class baseline, not a production accuracy guarantee.
- No factory CCTV footage or real camera stream has been used.
- Pipe bursts are not represented by a reliable dataset and are not detected by this model.
- ONVIF device discovery is not implemented; ONVIF use is URL/profile-based RTSP.
- Email/SMS/Telegram/WhatsApp require credentials.
- YOLOv8n is a general safety model, not a dedicated PPE/helmet/vest model.
- Production load and long-run camera stability have not been validated.
- GPU inference is supported when available but was not tested on a GPU here.

## Documentation

- [PROJECT_AUDIT.md](PROJECT_AUDIT.md)
- [IGL VisionGuard AI Complete Guide](docs_and_reports/IGL_VisionGuard_AI_COMPLETE_GUIDE.md)
- `backend/.env.example`
- `frontend/.env.example`
- `Dockerfile`
- `docker-compose.yml`