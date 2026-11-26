# IGL VisionGuard — Centralized AI Architecture Implementation Report

**Date:** 2026-11-23
**Phase:** Complete — Centralized AI Model Hub, Camera Command Centre, Automatic/Manual scene-based model switching
**Approach:** Consolidation and integration of the existing system, not replacement

> This report describes work performed on the existing IGL VisionGuard codebase. Existing
> functionality, models, cameras, database records, APIs and modules were preserved. No
> database was reset, no trained model was retrained or duplicated, and no working feature
> was removed.

---

## 1. Executive Summary

The existing IGL VisionGuard system already had a mature backend (FastAPI + SQLite + OpenCV +
Ultralytics YOLOv8), a React frontend, a camera manager with background inference workers, an
event engine, evidence capture, alerts, notifications, reports, RBAC and a Smart Entry & Exit
module.

The task was to **centralize** AI model management and camera-to-inference control, and to add
**Automatic (default) / Manual (optional) scene-based model switching** — without breaking any
existing functionality.

The implementation adds a central **AI Model Hub**, a **Camera Command Centre**, a
**Model-to-Camera Mapping** page, a **Model Training Centre**, a centralized **Detection & Alerts**
feed, and a **mode manager** that automatically selects compatible, *available* AI models based
on the camera's **actual live scene** (derived from real frame pixels, never from the camera name).

All 154 backend tests pass (136 pre-existing + 18 new) and the frontend production build passes.

---

## 2. What Was Already Available (Phase 1 Audit)

The audit confirmed the following already existed and were preserved:

| Area | Status before this work |
|---|---|
| FastAPI backend (`main.py`, `api_routes.py`, `security_routes.py`) | Working — 111 routes |
| SQLite DB (`igl_safety.db`) | Working — 36 tables, migrations v1–v5 |
| Model registry (`ai_models`, `model_versions`, `dataset_metadata`) | Working |
| Inference service (lazy YOLO loading, reload) | Working |
| Event engine (tracker, zones, dedup, evidence, incidents, alerts) | Working |
| Camera manager + background inference workers (`camera_worker.py`) | Working — live camera → inference loop already wired |
| Video adapters (RTSP/ONVIF/webcam/file/image/http) | Working |
| Camera credentials (Fernet-encrypted) | Working |
| Alerts, evidence, notifications, reports, audit logs, users, RBAC | Working |
| Water Leakage module (model v3 AVAILABLE, v1/v2 preserved) | Working |
| Pipe Inspection, Smart Entry & Exit, Thermal ingestion | Working |
| Frontend (React + Vite + Tailwind, 26+ views) | Working |

### Registered AI models at audit time

| model_id | Name | Status | Weights |
|---|---|---|---|
| `model-yolov8n` | YOLOv8n General Safety | **AVAILABLE** | `yolov8n.pt` |
| `model-water-leakage` | Water Pipeline Leakage | **AVAILABLE** (v3) | `model-water-leakage/v3/best.pt` |
| `model-thermal-person` | Thermal Person Detection | **NOT_TRAINED** | — |
| `model-pipe-corrosion` | Pipeline Corrosion Detection | **NOT_TRAINED** | — |

### Registered cameras at audit time

11 cameras (10 webcam + 1 file), all `DISCONNECTED` (no physical hardware attached).
No camera was reported online unless genuinely connected — this behavior is preserved.

---

## 3. What Was Centralized (Phase 2–5)

### 3.1 Centralized AI Model Hub (Phase 2)

**Backend:**
- `backend/model_registry.py` — single source of truth describing each model's capability:
  purpose, supported input families (RGB/THERMAL), scene affinities, and compatibility rules.
- `backend/orchestrator.py::model_hub()` — aggregates every registered model with its status,
  loading state, version history, supported classes and **model-to-camera usage**, all from real state.

**API (new):**
- `GET /api/ai/model-hub` — full Model Hub payload with summary counts.

**Frontend (new):**
- `frontend/src/views/ModelHubView.jsx` — "AI Model Hub" page showing every model, its
  status (AVAILABLE / NOT_TRAINED / NOT_DEPLOYED), loading state, classes, camera usage,
  version history and version activation/rollback.

Operators can now see all models in one place, check training status, loading status, versions,
and which cameras use each model. Untrained models show **NOT_TRAINED** and unavailable models
are clearly marked and never run.

### 3.2 Centralized Camera Command Centre (Phase 3)

**Backend:**
- `backend/orchestrator.py::camera_command_center()` — merges the camera registry with live
  worker state (scene, active models, frame freshness, last error) and recent detections.

**API (new):**
- `GET /api/ai/cameras` — every camera with live status, scene, active models, active
  detections, alert counts and health.

**Frontend (new):**
- `frontend/src/views/CameraCommandCenterView.jsx` — "Camera Command Centre" page showing each
  camera's live status, current scene + confidence, active models, frames processed, scene
  switch count, active detections and any worker error. Includes mode toggle and
  "re-select models" controls.

No camera is shown online unless it is genuinely streaming.

### 3.3 Centralized Detection & Alert System (Phase 5)

**Backend:**
- `backend/orchestrator.py::detections_feed()` — joins `detection_events` + `evidence_files` +
  `ai_models` + `cameras` into one normalized feed.

**API (new):**
- `GET /api/ai/detections` — centralized detection feed with camera, model, timestamp,
  confidence and evidence.

**Frontend (new):**
- `frontend/src/views/DetectionCenterView.jsx` — "Detection & Alerts" page with camera/event
  filters and evidence links.

The existing event engine (temporal tracking, zone checks, deduplication, evidence capture,
incident/alert creation, human review) is reused unchanged. Untrained/unavailable models never
produce events, so they never appear in this feed.

### 3.4 Model Training Centre (Phase 6 support)

**Frontend (new):**
- `frontend/src/views/TrainingCentreView.jsx` — training/validation/deployment status per
  model, active version, independent test metrics, per-class metrics and dataset provenance.

---

## 4. Automatic / Manual Mode + Scene-Based Switching (Phase 4)

This is the core new capability.

### 4.1 Scene Analyzer (`backend/scene_analyzer.py`)

Analyzes **actual frame pixels** (never the camera name or configured purpose) and returns a
scene label + confidence + reason:

- **Thermal** source type → `THERMAL` (authoritative, no pixel guessing).
- **Water-like** content → blue/cyan hue fraction + threshold → `WATER`.
- **Rust-like** content → warm hue fraction + threshold → `CORROSION`.
- **Skin-like** content → cheap frontal gate → `FACE`.
- Otherwise → `GENERAL` (low confidence, never fabricated).

### 4.2 Mode Manager (`backend/mode_manager.py`)

The single decision point answering "which models should run on this camera right now?".

- **AUTOMATIC (default):** selects every *available* model that is a compatible candidate for
  the detected scene + the camera's input family. Untrained/unavailable/incompatible models are
  never selected.
- **MANUAL (optional):** the operator pins an explicit model list; the manager still filters out
  unavailable, untrained and input-incompatible models at run time.
- **Hysteresis-stabilised switching:** a new scene must be observed `STABLE_SCENES_REQUIRED` (3)
  consecutive times before the active model set changes, so momentary frame noise cannot thrash
  model selection.
- **Thermal safety:** an RGB model is never selected for a thermal camera, and vice-versa.

### 4.3 Worker Integration (`backend/camera_worker.py`)

The existing per-camera background worker (which already ran inference on live frames) now:
1. Reads a live frame.
2. Runs `scene_analyzer.analyze_frame()` on the encoded frame.
3. Asks the `mode_manager` for the model set (Automatic or Manual).
4. Runs each selected model through the existing `event_engine.process_image()`.

The worker's `status()` now also reports live scene, scene confidence, active model ids, scene
switch count and last frame time — surfaced by the Camera Command Centre.

### 4.4 API (new)

| Endpoint | Purpose |
|---|---|
| `GET /api/ai/cameras/{id}/mode` | Current mode + last selection decision |
| `PUT /api/ai/cameras/{id}/mode` | Switch AUTOMATIC ↔ MANUAL |
| `PUT /api/ai/cameras/{id}/manual-models` | Pin manual model list (switches to MANUAL) |
| `POST /api/ai/cameras/{id}/reselect` | Force immediate re-evaluation |
| `GET /api/ai/model-camera-mapping` | Model-to-camera mapping with reasoning |

### 4.5 Model-to-Camera Mapping UI (new)

`frontend/src/views/ModelCameraMappingView.jsx` — "Model-to-Camera Mapping" page showing which
models are active on each camera and why, with manual assignment checkboxes (Manual mode) and a
one-click switch back to Automatic.

---

## 5. Existing Functionality Reused (not duplicated)

- **Event engine** — reused unchanged for all detection → event → incident → alert → evidence flow.
- **Inference service** — reused unchanged for model loading and YOLO inference.
- **Camera manager + workers** — reused; only the model-selection step inside the worker was upgraded.
- **Database** — all existing tables/records preserved. Mode is stored in the existing
  `cameras.config_json` (`mode` + `model_ids`), so no schema migration was required.
- **Models & checkpoints** — `model-water-leakage` v1/v2/v3 and `yolov8n.pt` preserved and reused.
- **Alerts, evidence, reports, notifications, RBAC, audit logs** — untouched and working.
- **Water Leakage, Pipe Inspection, Smart Entry & Exit** — untouched and working.
- **Frontend design/navigation** — existing pages preserved; 6 new pages added to the sidebar.

---

## 6. What Is New

| Component | File | Purpose |
|---|---|---|
| Model capability registry | `backend/model_registry.py` | Capability/scene/input contract per model |
| Scene analyzer | `backend/scene_analyzer.py` | Pixel-based scene detection |
| Mode manager | `backend/mode_manager.py` | Automatic/Manual selection + hysteresis |
| Orchestrator | `backend/orchestrator.py` | Model Hub, Camera Command, Mapping, Detections |
| Central API routes | `backend/api_routes.py` (`/api/ai/*`) | Model Hub, cameras, mapping, mode control, detections |
| AI Model Hub | `frontend/src/views/ModelHubView.jsx` | Centralized model management |
| Camera Command Centre | `frontend/src/views/CameraCommandCenterView.jsx` | Live camera + AI activity |
| Model-to-Camera Mapping | `frontend/src/views/ModelCameraMappingView.jsx` | Mapping + manual assignment |
| Detection & Alerts | `frontend/src/views/DetectionCenterView.jsx` | Centralized detections feed |
| Model Training Centre | `frontend/src/views/TrainingCentreView.jsx` | Training/validation status |
| Frontend API helpers | `frontend/src/services/api.js` | 8 new central API helpers |
| Tests | `backend/tests/test_central_architecture.py` | 18 tests for the new architecture |

---

## 7. Models: Available vs Untrained/Unavailable

| Model | Status | Detection behavior |
|---|---|---|
| `model-yolov8n` | **AVAILABLE** | Runs in Automatic mode on RGB cameras |
| `model-water-leakage` (v3) | **AVAILABLE** | Runs in Automatic mode; candidate for all RGB scenes |
| `model-thermal-person` | **NOT_TRAINED** | Never runs, never generates detections/alerts |
| `model-pipe-corrosion` | **NOT_TRAINED** | Never runs, never generates detections/alerts |

The two NOT_TRAINED models are correctly shown as unavailable and are excluded from both
automatic and manual selection. No fake detections were generated for them.

---

## 8. Cameras Actually Tested

- **Software integration:** 11 registered cameras (all `DISCONNECTED` — no physical hardware).
- **Real frame testing:** cameras backed by real image files (water-like, rust-like, general)
  were started through the actual camera worker to prove scene detection and model selection.
- **Hardware validation:** **pending** — no physical CCTV/thermal camera is available in this
  environment. RTSP/ONVIF support exists but is untested against a real authorized camera.

---

## 9. Automatic Mode Testing Results

End-to-end proof (real worker + real image files):

| Check | Result |
|---|---|
| Default mode is AUTOMATIC | PASS |
| Scene detected from actual pixels (WATER vs GENERAL) | PASS |
| Compatible available models auto-selected | PASS |
| Untrained models never selected | PASS |
| Scene switching stable (hysteresis, no thrash) | PASS |
| Scene change re-evaluates model set | PASS |

---

## 10. Manual Mode Testing Results

| Check | Result |
|---|---|
| Manual mode pins operator-selected models | PASS |
| Untrained/unavailable models filtered out even in Manual | PASS |
| Input-incompatible models rejected (RGB model on thermal, etc.) | PASS |
| Switch back to Automatic works | PASS |

---

## 11. Existing Modules Regression Testing

| Area | Result |
|---|---|
| Backend full test suite | **154 passed** (136 pre-existing + 18 new) |
| Camera worker tests (existing) | **6 passed** |
| Water Leakage module | Unchanged, passing |
| Pipe Inspection / Smart Entry & Exit / Thermal | Unchanged, passing |
| Auth / RBAC / evidence / reports / notifications | Unchanged, passing |
| Frontend production build | **Passed** |
| Database integrity | Preserved; no reset, no destructive migration |

---

## 12. Pending Hardware & Real-World Validation

The following require physical hardware / authorized network access and are **not** claimed as
tested:

1. Real CCTV / RTSP / ONVIF camera connection.
2. Thermal camera + calibrated radiometric source.
3. Genuine factory footage evaluation of any model.
4. Production load and long-run camera stability.
5. RFID / access-control / turnstile / alarm hardware.

---

## 13. Backend and Frontend Test Results

- **Backend:** `cd backend && .venv\Scripts\python.exe -m pytest -q` → **154 passed**.
- **Frontend:** `cd frontend && npm run build` → **production build passed**.
- **New architecture E2E:** 12/12 checks passed (Automatic + Manual + scene switching).

---

## 14. Acceptance Against the Requested Phases

| Phase | Requirement | Status |
|---|---|---|
| 1 | Complete existing system audit | Done (§2) |
| 2 | Centralized AI Model Hub | Done (§3.1) |
| 3 | Centralized Camera Command Centre | Done (§3.2) |
| 4 | Automatic (default) + Manual + scene switching | Done (§4) |
| 5 | Centralized detection & alert system | Done (§3.3) |
| 6 | Frontend integration (6 new pages) | Done (§6) |
| 7 | Existing functionality preservation | Done (§5, §11) |
| 8 | Testing and verification | Done (§9–§13) |
| 9 | Final report | This document |

---

## 15. How to Use

1. Open **AI Model Hub** to see every model, its status, versions and camera usage.
2. Open **Camera Command Centre** to see live cameras, scenes, active models and detections.
3. Open **Model-to-Camera Mapping** to assign models manually (Manual mode) or use Automatic.
4. Open **Detection & Alerts** for the centralized detection feed with evidence.
5. Open **Model Training Centre** for training/validation status.
6. Each camera defaults to **AUTOMATIC**; use the mode toggle to switch to Manual when needed.

No retraining, no database reset, no camera reconfiguration, and no existing API was removed.