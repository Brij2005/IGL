# IGL VisionGuard — Complete Codebase Audit & Integration Plan

Date: 2026-11-22
Phase: 1 (Audit) — pre-implementation

---

## 1. Inventory

- Backend source files inspected: 22 (excluding tests, data, models, caches)
- Frontend source files inspected: 27 (5 services, 26 views, 4 components, context, App)
- Database tables: 31 (operational DB at migration v3; migration v4 defined but not yet applied to `igl_safety.db`)
- AI models registered: 2 (`model-yolov8n` AVAILABLE, `model-water-leakage` v3 AVAILABLE); `model-thermal-person` defined in code (migration 4) but not yet in live DB.

---

## 2. File Classification

### 2.1 Fully working and integrated
| File | Role | Status |
|---|---|---|
| `backend/main.py` | FastAPI app, legacy REST endpoints | Working |
| `backend/api_routes.py` | Operations API (cameras, zones, models, events, reports, etc.) | Working |
| `backend/database.py` | SQLite schema + migrations 1–4 | Working |
| `backend/inference_service.py` | Model registry + YOLO inference | Working |
| `backend/event_engine.py` | Detection → event/incident/alert + evidence | Working |
| `backend/camera_manager.py` | Camera lifecycle, reconnect, MJPEG preview | Working (preview only) |
| `backend/video_sources.py` | RTSP/ONVIF/webcam/file/image/http/mock adapters | Working |
| `backend/security.py` | Auth, RBAC, password hashing, encryption | Working |
| `backend/notification_service.py` | Email/SMS/Telegram/WhatsApp/in-app | Working |
| `backend/reports.py` | Daily/weekly/monthly reports | Working |
| `backend/whatsapp_service.py` | WhatsApp Cloud API (mock/live) | Working |
| `backend/leakage_training.py` | Dataset prep + training + eval | Working |
| `backend/evaluate_leakage.py` | Independent test evaluation | Working |
| `backend/hardware_adapters.py` | 7 hardware adapters (honest NOT_CONNECTED) | Working |
| `backend/security_monitoring.py` | Entry/exit, assets, thermal, recorded video | Working |
| `backend/security_routes.py` | 15 security endpoints | Working |
| `frontend/src/App.jsx` | Tab router | Working |
| `frontend/src/context/SafetyContext.jsx` | Global state + backend polling | Working |
| `frontend/src/services/api.js` | All backend API helpers | Working |
| `frontend/src/views/SecurityDashboardView.jsx` | Smart Entry & Exit page | Working |
| `frontend/src/views/LeakageView.jsx`, `ModelsView.jsx`, `ReportsView.jsx`, `EvidenceView.jsx`, `PlantView.jsx`, `CameraManagementView.jsx`, `CameraSetupView.jsx`, `SystemHealthView.jsx`, `AuditLogView.jsx`, `UsersView.jsx`, `SettingsView.jsx`, `NotificationsView.jsx` | Ops pages wired to backend | Working |

### 2.2 Implemented but DISCONNECTED (key gaps)
| File / System | Issue |
|---|---|
| **Camera → inference pipeline** | `camera_manager` captures frames + serves MJPEG, but **no background worker runs inference on live frames**. `event_engine.process_image()` is only called by manual `/events/process` and recorded-video eval. Live cameras never auto-generate events/incidents/alerts. |
| **`model-thermal-person`** | Defined in migration 4 but **not yet applied to operational DB**; thermal inference path exists but model row missing in live DB. |
| **Migration 4 not applied** | `igl_safety.db` is at v3; `asset_register`, `access_records`, `thermal_readings`, `video_analyses`, `gate_passes`, `asset_movements` tables absent in live DB until app restart runs `init_db()`. |
| **`frontend/src/services/realVisionProcessor.js`** | Runs **client-side TensorFlow.js COCO-SSD** as a parallel AI path, separate from the backend inference pipeline. Used by `CameraInputView` and `ExpectedPersonnelView`. Not unified with backend models/events. |
| **`frontend/src/views/LiveMonitoringView.jsx`** | Uses client-side detection + random track IDs (`Math.random`) for display; not driven by backend `detection_events`. |

### 2.3 Partially working
| File | Note |
|---|---|
| `backend/camera_manager.py` | Preview/reconnect works; no inference hook. |
| `frontend/src/views/CameraInputView.jsx` | Webcam works locally; uses client-side TFJS, not backend events. |
| `frontend/src/views/PersonnelMonitoringView.jsx` / `ExpectedPersonnelView.jsx` | Functional but rely on simulated tracks for demo; backend personnel endpoints exist but UI uses local simulation for the "add person" demo buttons. |

### 2.4 Duplicate implementation
| Duplication | Detail |
|---|---|
| Person/object detection | Backend YOLO (`/api/yolo/detect`, `/api/models/{id}/detect`) **and** client-side TFJS COCO-SSD (`realVisionProcessor.js`). Two AI paths. |
| Frame quality | `backend/ai_engine.py::assess_frame_quality` **and** `frontend/realVisionProcessor.analyzeFrameQuality` (client-side). |
|restricted zones | `restricted_zones` table **and** `monitoring_zones` (zone_type=RESTRICTED). Both exist. |

### 2.5 Unused / orphaned (not imported by app runtime)
| File | Note |
|---|---|
| `backend/debug_yolo_phone.py` | Dev CLI debug script. Not imported. Keep as dev tool. |
| `backend/test_yolo_api.py` | Dev CLI API tester. Not imported. Keep as dev tool. |
| `backend/compare_leakage_models.py` | Dev CLI comparison tool. Not imported. Keep. |
| `frontend/src/views/ImageCameraInspectionView.jsx` | Routed via `PipeInspectionView` (intentional). Not orphaned in practice. |
| `frontend/src/services/packagingProcessor.js` | Explicit demo simulator; used by `PackagingInspectionView` (labeled DEMO). |

### 2.6 Demo / mock / placeholder (honestly labeled)
| File | Label |
|---|---|
| `packagingProcessor.js` + `PackagingInspectionView.jsx` | "DEMO ONLY — NO REAL INSPECTION" |
| `whatsapp_service.py` | `WHATSAPP_MODE=mock` default, reports `SIMULATED` |
| `PersonnelMonitoringView.jsx` / `ExpectedPersonnelView.jsx` | Simulated track buttons for testing (labeled) |

### 2.7 Broken or missing dependency
| Item | Note |
|---|---|
| `backend/data/leakage_multisource/...` | Broken symlink/encoding artifacts in dataset dir (not source; gitignored data). Harmless to app. |
| `frontend/public/models/helmet/model.json` | Referenced by `realVisionProcessor` but file absent → helmet model fails to load (graceful). |

---

## 3. Architecture Verification (Phase 2 target)

Current real flow for **recorded video** (works):
`Upload → /api/security/analyze-video → decode frames → inference_service.detect_image → SimpleTracker → record_access + evidence → video_analyses → dashboard`

Current real flow for **live camera** (BROKEN — no inference):
`Camera → camera_manager (capture + MJPEG preview) → browser. NO event/incident/alert generation.`

**Gap to close:** a background worker that, per running camera, captures frames → runs the camera's configured model → `event_engine.process_image` → events/incidents/alerts/evidence → dashboard. This is the core Phase 3 work.

---

## 4. Database Unification (Phase 5)

- All modules use the single shared `igl_safety.db` via `database.get_db_connection()`. Good.
- FK integrity: `detection_events.camera_id → cameras.id`, `evidence_files.event_id`, `leakage_incidents.event_id`, `alerts.event_id` — consistent.
- `asset_movements.asset_id → asset_register.id`, `gate_passes.asset_id → asset_register.id` — consistent (migration 4).
- **Action needed:** apply migration 4 to `igl_safety.db` (safe, idempotent, via `init_db()` on startup). No destructive migration.
- No duplicate tables requiring reconciliation; `restricted_zones` + `monitoring_zones(RESTRICTED)` coexist by design (legacy + new). Acceptable.

---

## 5. Frontend/Backend Connection (Phase 6)

- 26 nav items → 26 views rendered → all import existing files. Verified.
- All ops pages call real backend APIs via `api.js`. Verified for Security, Leakage, Models, Reports, Evidence, Plant, Camera Management, System Health, Audit, Users, Settings, Notifications.
- Demo pages (Packaging, Personnel Monitoring, Expected Personnel) use local simulation for demo buttons but clearly labeled; their backend endpoints exist.
- `realVisionProcessor` client-side AI is a parallel path — not "fake," but not unified with backend events. Candidate for unification (route live detection through backend).

---

## 6. Security & Reliability (Phase 8) — current state

- Auth: optional (`ENABLE_AUTH=false` default), RBAC enforced when enabled. Verified by `test_auth_enabled.py`.
- Evidence/sensitive APIs: auth-protected via middleware when enabled.
- Input validation: Pydantic models + manual checks on security routes.
- Secrets: env vars via `config.py` + `.env` (gitignored). `SECRET_KEY` has dev default.
- Error handling: consistent HTTPException usage.
- **Gap:** no background worker lifecycle (start/stop) exists yet — must be added with the camera inference worker, with safe shutdown.

---

## 7. Integration Plan (safe, incremental)

1. **Apply migration 4** to operational DB by running `init_db()` (idempotent; adds 6 tables + thermal model). Verify no data loss.
2. **Build camera inference worker** (`camera_worker.py`): background thread per running camera that captures frames at FPS, runs the camera's configured model via `inference_service`, calls `event_engine.process_image(temporal=True)`, and stops cleanly on camera stop / app shutdown. Honest errors when model unavailable.
3. **Wire worker lifecycle** into `camera_manager.start/stop` and FastAPI startup/shutdown.
4. **Add `model-thermal-person` enforcement**: ensure RGB model is never used on thermal cameras (already enforced in security_monitoring; verify worker respects source_type).
5. **Unify live detection**: add a backend endpoint the frontend can use for live events, OR document that client-side TFJS is a local preview only (not operational events). Prefer routing operational events through backend worker.
6. **Run full test suite + E2E** after each change.
7. **Update docs** with final delivery report.

No new features. No deletion of working files. No fabricated data. No claim of live factory validation.

---

## 8. Risk Notes

- Background worker introduces concurrency; must guard DB access (SQLite WAL already enabled) and avoid duplicate processing (event engine dedup already handles).
- Worker must not start for `mock`/`image` sources in production (image is single-frame).
- Migration 4 is additive-only; safe to apply.