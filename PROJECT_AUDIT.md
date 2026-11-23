# IGL VisionGuard — Project Audit and Completion Report

Date: 2026-11-20  
Backup created before V3 changes: `.dist/backup/20261121-182642/`  
Baseline before changes: backend compiled; frontend `npm run build` passed.

## 1. Scope Delivered

The existing repository was preserved and extended rather than replaced.

### Backend

- FastAPI application remains at `backend/main.py`.
- SQLite remains the operational database at `backend/igl_safety.db` (override with `DATABASE_PATH`).
- Added a versioned schema/migration path in `backend/database.py`.
- Added authenticated camera management, zone configuration, model registry, event engine, evidence storage, notifications, reports, audit logs, user management and system health.
- Added a backend-decoded camera stream endpoint (MJPEG) so the browser does not attempt to play RTSP directly.
- Added encrypted-at-rest camera credential storage.
- Added global authentication middleware and role-based dependencies.
- Added password hashing with PBKDF2-HMAC-SHA256 and signed bearer tokens.
- Added rate limiting on login.

### New backend modules

- `config.py` — environment settings and runtime directories.
- `security.py` — password hashing, token signing/decoding, role dependencies, rate limiting, audit helper, credential encryption.
- `video_sources.py` — common adapters for RTSP, ONVIF/RTSP, webcam, file, image, public HTTP and a clearly labeled integration-test mock.
- `camera_manager.py` — camera lifecycle, reconnect handling, frame freshness, health logging and connection testing.
- `inference_service.py` — model registry loading and normalized detection output.
- `event_engine.py` — temporal tracking, zone checks, deduplication, evidence capture, detection events, incidents and alerts.
- `notification_service.py` — in-app, email, SMS, Telegram, WhatsApp and retry/delivery logging.
- `reports.py` — daily/weekly/monthly/incident reports from stored records.
- `leakage_training.py` — dataset validation, cleaning, deduplication, class remapping, train/val/test preparation, dataset registration, training, evaluation and recorded-video inference CLI.
- `download_leakage_dataset.py` — genuine Roboflow export download helper. It reads `ROBOFLOW_API_KEY` from `backend/.env` or the process environment and never prints the key.
- `api_routes.py` — new operations API surface mounted under `/api`.

### New frontend views

- `LoginView.jsx`
- `CameraManagementView.jsx`
- `CameraSetupView.jsx`
- `LeakageView.jsx`
- `EvidenceView.jsx`
- `PlantView.jsx`
- `ModelsView.jsx`
- `UsersView.jsx`
- `ReportsView.jsx`
- `UsersView.jsx`
- `NotificationsView.jsx`
- `SystemHealthView.jsx`
- `AuditLogView.jsx`
- `SettingsView.jsx`

The existing dashboard, live monitoring, safety, personnel, alerts, analytics and configuration pages were preserved. New operations pages are wired to real backend endpoints.

### Deployment

- Added `Dockerfile` (frontend build + Python backend + static serving).
- Added `docker-compose.yml` with persistent volumes for database, evidence and models.
- Added `backend/.env.example` and `frontend/.env.example`.

## 2. Camera and Video Architecture

Supported source types:

- `rtsp`
- `onvif` (URL/profile-based; ONVIF device discovery is not implemented)
- `webcam`
- `file`
- `image`
- `http`
- `mock` (test-only adapter; not exposed through the production camera registration API)

Every source goes through the same backend capture adapter. Frames are decoded by OpenCV, quality-assessed, encoded for preview/evidence, and passed to the inference service.

Camera management supports:

- registration
- connection test
- start/stop
- reconnect attempts
- frame freshness
- resolution/FPS configuration
- status and health
- plant location and pipeline mapping
- zone drawing and persistence
- authenticated MJPEG preview
- encrypted credentials
- audit logging

## 3. Water Leakage AI Status

### Implemented

- Six required classes are defined in the model registry:
  - Water Dripping
  - Water Spraying
  - Visible Water Flow
  - Pipe Joint Leakage
  - Water Accumulation
  - Normal/Dry Pipeline
- Dataset validation, cleaning, duplicate removal, class remapping to the six canonical classes, deterministic train/val/test preparation, training, evaluation, recorded-video inference and model registration scripts are implemented.
- `prepare` creates a genuine YOLO dataset split with a dataset report; it does not generate images or annotations.
- `train` prepares a raw genuine export automatically, registers the prepared dataset, trains YOLO, saves `best.pt`, updates `ai_models`, and inserts a `model_versions` record only after successful training.
- `eval` evaluates the independent test split and can persist metrics against a model ID.
- `video` runs recorded-video inference and writes an annotated video plus JSON metrics.
- Inference service rejects the leakage model unless a trained model is registered.
- Inference service supports model reload so a newly trained model can be picked up without restarting the process.
- Event engine creates leakage incidents with status `POSSIBLE`; it never automatically confirms a leak.
- Confirmation, suspicion and dismissal require an authorized human review endpoint.
- Water accumulation alone cannot become a confirmed pipe leakage.

### Dataset research

A genuine public dataset family was found on Roboflow Universe:

- `water-leakage-detection/water-leakage` v4 (CC BY 4.0)
- `kai-bsnlf/water-leakage-bnrzx` v1 (CC BY 4.0)
- `jaishreeram/pipeline_leakage1` v1 (CC BY 4.0)
- `onekode3/pipeline_leak1` v1 (CC BY 4.0)

`leak-adhw8/water-leak-wgdaq` v3 was inspected but excluded because its exported class name is only `0` and could not be semantically verified. No unrelated classes were relabeled.

Exporting these datasets requires a Roboflow account/API key. The downloaded datasets and generated weights are gitignored and are not bundled in this repository. No dataset content was fabricated.

### Current model status

`model-water-leakage` is registered as `AVAILABLE` with `active_version = 3` and `weights_path = model-water-leakage/v3/best.pt`. Versions 1 and 2 are preserved for rollback.

Version 3 is a six-class baseline trained on a combined genuine multi-source dataset. It covers all six configured classes, but Water Dripping and Water Spraying remain weak and are not reliable production detectors yet.

Actual dataset and training results:

- Combined dataset: 12,685 images, 12,685 labels, 110 duplicate images removed, 9 missing-label images skipped, zero malformed annotations.
- Source-aware split: 7,931 train / 2,377 validation / 2,377 independent test.
- Balanced training set: 8,963 train images after deterministic oversampling of Water Dripping and Water Spraying; validation and test unchanged.
- Class distribution: Water Dripping 255, Water Spraying 582, Visible Water Flow 2,025, Pipe Joint Leakage 4,236, Water Accumulation 6,468, Normal/Dry Pipeline 2,574.
- Training: YOLOv8n, 4 epochs, 192px, batch 32, CPU (Intel i5-1035G1, no CUDA), 3,182.8 seconds.
- Validation metrics: precision 0.334, recall 0.343, mAP50 0.242, mAP50-95 0.144.
- Independent test metrics: precision 0.476, recall 0.269, mAP50 0.185, mAP50-95 0.127.
- Test mAP50 by class: Water Dripping 0.0669, Water Spraying 0.0226, Visible Water Flow 0.0850, Pipe Joint Leakage 0.2941, Water Accumulation 0.5282, Normal/Dry Pipeline 0.1151.
- Inference speed: 14.2 ms per image on CPU.
- Version 1 model: `MV-F8B4DC55`, dataset `DS-9CB78E42`.
- Version 2 model: `MV-71201A47`, dataset `DS-4253D46A`.
- Version 3 model: `MV-9D9BEA03`, dataset `DS-0DEB337E`.
- Model card: `docs_and_reports/WATER_LEAKAGE_MODEL_CARD.json`.
- Comparison: `docs_and_reports/LEAKAGE_MODEL_COMPARISON.md`.
- Detailed V3 evaluation: `docs_and_reports/evaluations/v3/evaluation.md`.

### V1 / V2 / V3 comparison on the same independent test split

| Version | Precision | Recall | mAP50 | mAP50-95 |
|---|---:|---:|---:|---:|
| V1 | 0.0628 | 0.0529 | 0.0419 | 0.0267 |
| V2 | 0.2791 | 0.2197 | 0.1377 | 0.0874 |
| V3 | 0.4760 | 0.2689 | 0.1853 | 0.1271 |

V3 has the best aggregate precision, recall and mAP, and is the only version with non-zero Water Dripping and Water Spraying mAP. V1 and V2 remain preserved for rollback.

### Genuine video testing

Two genuine public water-leak videos from `freedomwebtech/yolo11-water-leak` were used for inference testing (not factory CCTV):

- `water_leak_5.mp4`: 300 frames, 0 detections at confidence 0.25; 46 `Water Spraying` predictions at confidence 0.10.
- `water_leak_6.mp4`: 173 frames, 6 `Water Accumulation` detections at confidence 0.25; 26 predictions at confidence 0.10 (20 `Water Accumulation`, 6 `Water Spraying`).

After the event-deduplication fix, the same `water_leak_5.mp4` event-path test produced 4 stored detection events, 4 evidence files and 2 alerts. Those temporary evaluation records were removed from the operational database after verification. This is not equivalent to factory CCTV validation.

### Retraining and evaluation

1. Export a genuine YOLO-format dataset.
2. Validate it:

   ```bash
   python backend/leakage_training.py validate --dataset-root /path/to/dataset
   ```

3. Prepare, clean, deduplicate and split each source:

   ```bash
   python backend/leakage_training.py prepare --dataset-root /path/to/dataset
   ```

4. Combine sources with explicit class mapping and source-group-aware splits:

   ```bash
   python backend/leakage_training.py combine --config backend/data/leakage_multisource/combine_config.json --output-root backend/data/leakage_multiclass
   ```

5. Rebalance weak classes in the training split only:

   ```bash
   python backend/leakage_training.py oversample --dataset-root backend/data/leakage_multiclass --output-root backend/data/leakage_multiclass_balanced --classes 'Water Dripping,Water Spraying' --factor 4
   ```

6. Train a new version:

   ```bash
   python backend/leakage_training.py train --dataset-root backend/data/leakage_multiclass_balanced --prepared-root backend/data/leakage_multiclass_balanced --model-id model-water-leakage --version 3 --epochs 4 --imgsz 192 --batch 32
   ```

7. Evaluate on the independent test split:

   ```bash
   python backend/evaluate_leakage.py --weights backend/models/model-water-leakage/v3/best.pt --dataset-root backend/data/leakage_multiclass_balanced --split test --imgsz 192 --conf 0.25 --output-dir backend/docs_and_reports/evaluations/v3
   ```

8. Reload the model:

   ```bash
   curl -X POST http://127.0.0.1:8000/api/models/model-water-leakage/reload
   ```

The model must be validated, trained and evaluated on genuine data before it is treated as operational.

## 4. Event Engine

The event engine:

- accepts genuine model predictions only
- applies per-camera confidence and temporal thresholds
- tracks objects across frames with a simple centroid tracker
- suppresses duplicate events/alerts using cooldown and deduplication keys
- associates events with camera, plant location, pipeline and zone
- captures evidence and stores it in `backend/evidence`
- creates detection events, incidents and alerts
- does not auto-confirm leakage
- supports human review and false-positive/negative recording

## 5. Alerts and Notifications

Implemented provider integrations:

- in-app (database-backed)
- email via SMTP
- SMS via configurable provider URL
- Telegram
- WhatsApp (existing backend service, mock mode by default)
- delivery history and retry attempts

Unconfigured providers are reported as `NOT_CONFIGURED`. Provider acceptance is recorded separately from local storage success. A provider is never reported as successful unless it accepts the message.

## 6. Database

Existing tables were preserved. New tables and columns include:

- `cameras` — plant/pipeline mapping, source options, credentials, config
- `plant_locations`
- `pipelines`
- `monitoring_zones`
- `ai_models`
- `model_versions`
- `dataset_metadata`
- `detection_events`
- `leakage_incidents`
- `safety_incidents`
- `evidence_files`
- `alert_history`
- `notification_deliveries`
- `notification_history`
- `user_reviews`
- `audit_logs`
- `reports`
- `users`
- `app_settings`
- `schema_migrations`

Indexes were added for cameras, alerts, observations, zones, events, evidence, audit logs and notifications.

## 7. Security

- Authentication is implemented as optional backend token authentication and RBAC. It is disabled by default (`ENABLE_AUTH=false`) so the current dashboard opens directly; production deployments can set `ENABLE_AUTH=true` and use `POST /api/auth/login`.
- A default admin account is created on first startup from `ADMIN_USERNAME`/`ADMIN_PASSWORD`.
- Default development credentials are documented and must be changed in production.
- Passwords use PBKDF2-HMAC-SHA256 with per-user salt.
- Tokens are signed with HMAC-SHA256 and expire.
- Camera credentials are encrypted with Fernet using a key derived from `SECRET_KEY`.
- Camera credentials are not returned to the frontend.
- Evidence and report access is authenticated.
- Login is rate limited.
- Sensitive settings are not accepted through the general settings endpoint.

## 8. Tests and Verification

### Backend

Command:

```bash
cd backend
.venv\Scripts\python.exe -m pytest -q
```

Result: **23 passed** (3 deprecation warnings from FastAPI/Starlette TestClient).

Covered:

- root and system health
- password hashing and token decode
- login and auth middleware
- camera registration
- zone save/load
- camera configuration save
- camera lifecycle: test, start, frame, health, stop
- real YOLOv8 inference on a generated test image
- leakage model correctly reported unavailable before training
- trained leakage model inference after registration (skipped only if weights are not present)
- event pipeline accepts real image input
- notification failure is not reported as success
- report generation and download
- mock adapter (test-only)
- evidence upload and retrieval
- auth-enabled API protection (subprocess test)
- leakage dataset validation, class remapping, deduplication, split generation and malformed annotation rejection
- polygon annotation conversion and unmapped-class dropping
- event deduplication with UTC SQLite timestamps
- model reload endpoint
- video-evaluation endpoint rejects an untrained leakage model with HTTP 409

### Frontend

Command:

```bash
cd frontend
npm run build
```

Result: **production build passed**.

### Manual model smoke

A genuine public image from the Ultralytics sample set was used once to verify that the real YOLOv8 model and event engine produce detections/events. This was a software integration check, not leakage model evaluation and not a replacement for recorded factory footage.

## 9. Known Limitations / Missing External Inputs

1. No physical factory CCTV camera is available. RTSP/ONVIF support is implemented but has not been tested against a real authorized camera.
2. No genuine recorded factory footage is bundled. Model evaluation on factory footage has not been performed.
3. The leakage model covers all six classes, but Water Dripping and Water Spraying have very low test mAP and are not reliable production detectors yet.
4. ONVIF device discovery is not implemented. Supported ONVIF use is URL/profile-based RTSP.
5. Email, SMS, Telegram and WhatsApp require deployment credentials. The default WhatsApp mode is mock and is labeled as such.
6. Existing general safety detection uses YOLOv8n COCO classes where applicable. It is not a dedicated helmet/vest/PPE model unless such a model is supplied.
7. The dashboard is fully responsive and uses backend data on the new operations pages. Some legacy pages retain their original presentation; no new fabricated operational data was added.
8. Production scale, load and long-run camera stability have not been validated in a factory environment.
9. GPU inference is supported by Ultralytics when available, but no GPU deployment was tested here.
10. `npm run build` emits a chunk-size warning due to the existing face-api dependency; the build still passes.

## 10. Camera Installation Workflow

When an authorized camera is available:

1. Sign in with an administrator or operator account.
2. Open **Camera Management**.
3. Enter a camera name and authorized RTSP URL (or ONVIF/RTSP URL).
4. Enter username/password if the camera requires them.
5. Register the camera.
6. Click **Test** and verify a real preview.
7. Set resolution/FPS in **Camera Angle & Zones**.
8. Start the camera.
9. Adjust the physical camera angle at installation.
10. Refresh the backend frame preview.
11. Draw the pipeline monitoring polygon.
12. Select the relevant models and save the zone.
13. Map the camera to a plant location and pipeline.
14. Save configuration and start monitoring.

No new application development is required for ordinary camera onboarding.

## 11. Acceptance Checklist

- [x] Existing FastAPI backend preserved.
- [x] Existing React frontend preserved and extended.
- [x] Backup created before changes.
- [x] Database migrations/schema added.
- [x] Camera adapters implemented.
- [x] Camera configuration and zones persist.
- [x] Backend-decoded camera preview implemented.
- [x] Leakage dataset preparation, cleaning, deduplication, class remapping and train/val/test split implemented.
- [x] Leakage training/evaluation/recorded-video pipeline implemented and tested at the software-integration level.
- [x] Leakage model trained and registered from genuine public datasets (active version 3; v1/v2 preserved).
- [x] Independent test-split evaluation completed and class-wise metrics recorded.
- [x] Genuine public water-leak video inference and event/evidence path verified.
- [x] Event deduplication corrected for UTC SQLite timestamps.
- [x] Event engine and evidence capture implemented.
- [x] Alert acknowledgement/resolution preserved.
- [x] Notification providers and retry handling implemented.
- [x] Reports generated from stored data.
- [x] Auth, RBAC, audit logs and credential encryption implemented.
- [x] New dashboard pages wired to backend.
- [x] Backend tests pass.
- [x] Frontend production build passes.
- [x] Deployment files added.
- [ ] Real CCTV camera connection verified.
- [ ] Trained leakage model evaluated on genuine factory footage.
- [ ] Production load test completed.

The unchecked items require physical hardware, authorized network access, genuine dataset export and/or factory footage. They are not software gaps that can be fabricated.