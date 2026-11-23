# IGL VisionGuard — Complete Live Website & Feature Mapping Audit

Audit date: 2026-11-22  
Project: `C:\Users\Krishna Sharma\Desktop\IGL`  
Frontend: `http://192.168.0.106:3000/`  
Backend: `http://127.0.0.1:8000/`  
Mode: **READ-ONLY audit**. No application code, database records, or configuration were changed during this audit.

## 1. Audit Method

- Inspected frontend route/state mapping in `frontend/src/App.jsx`.
- Inspected all sidebar items in `frontend/src/components/Sidebar.jsx`.
- Inspected frontend views in `frontend/src/views/`.
- Inspected backend routes in `backend/main.py` and `backend/api_routes.py`.
- Inspected database tables and record counts in `backend/igl_safety.db`.
- Inspected model registry, versions, weights and evaluation data.
- Probed live frontend and backend over HTTP.
- Checked model health and one real inference request against V3.

### Browser limitation

A full browser navigation/console pass was **not performed** because browser automation tooling is not installed in the project. The frontend HTML and backend APIs were verified over HTTP, and page behavior was verified from source. Browser-level console errors and visual rendering were not independently verified.

## 2. Live HTTP Verification

| Check | Result |
|---|---|
| Frontend `http://192.168.0.106:3000/` | HTTP 200, HTML title `IGL VisionGuard AI — Industrial AI Safety Intelligence` |
| Backend `http://127.0.0.1:8000/` | HTTP 200, `{"system":"IGL Industrial AI Safety Server","status":"ONLINE"}` |
| `/api/dashboard/stats` | HTTP 200 |
| `/api/cameras` | HTTP 200, `[]` |
| `/api/alerts` | HTTP 200, `[]` |
| `/api/models` | HTTP 200 |
| `/api/models/model-water-leakage/health` | HTTP 200, `loaded: true` |
| `/api/models/model-water-leakage/evaluation` | HTTP 200, active version 3 |
| `/api/incidents/leakage` | HTTP 200, `[]` |
| `/api/leakage/incidents` | HTTP 404 — not the frontend's path; frontend uses `/api/incidents/leakage` |
| `/api/evidence` | HTTP 200, `[]` |
| `/api/reports` | HTTP 200, `[]` |
| `/api/audit-logs` | HTTP 200, 13 records |
| `/api/users` | HTTP 200, 1 user |
| `/api/notifications/status` | HTTP 200; only in-app configured |
| `/api/system/health` | HTTP 200 |

## 3. Database Verification

| Table | Records |
|---|---:|
| `cameras` | 0 |
| `monitoring_zones` | 0 |
| `restricted_zones` | 0 |
| `detection_events` | 0 |
| `leakage_incidents` | 0 |
| `safety_incidents` | 0 |
| `alerts` | 0 |
| `evidence_files` | 0 |
| `reports` | 0 |
| `audit_logs` | 13 |
| `users` | 1 |
| `model_versions` | 3 |
| `dataset_metadata` | 7 |
| `plant_locations` | 1 |
| `pipelines` | 1 |
| `personnel_monitoring_rules` | 1 |
| `personnel_monitoring_events` | 0 |
| `expected_personnel_rules` | 1 |
| `observations` | 0 |

The operational database is mostly empty. There are no cameras, alerts, incidents, evidence files or reports. The model registry and audit logs are populated.

## 4. Sidebar / Feature Mapping

The app uses state-based navigation (`activeTab`), not React Router URL paths. The "Frontend route" below is the actual `activeTab` value.

| # | Section | Frontend route (`activeTab`) | Component file | Backend API | Database | Actual status |
|---:|---|---|---|---|---|---|
| 1 | Overview | `dashboard` | `views/DashboardView.jsx` | `/api/dashboard/stats`, `/api/alerts`, `/api/cameras`, `/api/personnel-monitoring/events` | `alerts`, `cameras`, `personnel_monitoring_events` | PARTIALLY IMPLEMENTED |
| 2 | Camera Setup | `camera-input` | `views/CameraInputView.jsx` | `/api/cameras`, `/api/cameras/{id}/start|stop|test`, `/api/cameras/test-source` | `cameras`, `health_logs` | IMPLEMENTED BUT NOT VERIFIED |
| 3 | Live Monitoring | `live-monitoring` | `views/LiveMonitoringView.jsx` | `/api/process-frame`, `/api/yolo/detect`, `/api/alerts`, `/api/observations`, `/api/registered-personnel` | `alerts`, `observations` | PARTIALLY IMPLEMENTED |
| 4 | Expected Personnel | `expected-personnel` | `views/ExpectedPersonnelView.jsx` | `/api/expected-personnel/rules`, `/api/notifications/whatsapp/status|test|send`, `/api/notifications/logs` | `expected_personnel_rules`, `notification_logs` | PARTIALLY IMPLEMENTED |
| 5 | Packaging Inspection | `packaging` | `views/PackagingInspectionView.jsx` | None | None | UI ONLY |
| 6 | Pipe Inspection & Scanner | `pipe-inspection` | `views/PipeInspectionView.jsx` | `/api/alerts`, `/api/observations` | `alerts`, `observations` | PARTIALLY IMPLEMENTED |
| 7 | Personnel Schedule & Monitoring | `personnel-monitoring` | `views/PersonnelMonitoringView.jsx` | `/api/personnel-monitoring/rules`, `/api/personnel-monitoring/events`, `/api/alerts` | `personnel_monitoring_rules`, `personnel_monitoring_events`, `alerts` | PARTIALLY IMPLEMENTED |
| 8 | Safety Events | `alerts` | `views/AlertsView.jsx` | `/api/alerts`, `/api/alerts/{id}/acknowledge`, `/api/alerts/{id}/resolve` | `alerts`, `alert_history` | IMPLEMENTED BUT NOT VERIFIED |
| 9 | Near Miss | `near-miss` | `views/NearMissView.jsx` | `/api/alerts` via `dispatchAlert` | `alerts` | PARTIALLY IMPLEMENTED |
| 10 | Incidents | `personnel` | `views/PersonnelAnomalyView.jsx` | `/api/observations`, `/api/personnel/check-anomaly` | `observations` | PARTIALLY IMPLEMENTED |
| 11 | Analytics | `analytics` | `views/AnalyticsView.jsx` | `/api/analytics` | `alerts` | IMPLEMENTED BUT NOT VERIFIED |
| 12 | Camera & AI Health | `camera-health` | `views/CameraHealthView.jsx` | None (local `streamMetrics`) | None | PARTIALLY IMPLEMENTED |
| 13 | Camera Management | `camera-management` | `views/CameraManagementView.jsx` | `/api/cameras`, `/api/cameras/{id}/start|stop|test|config`, `/api/cameras/{id}/stream.mjpg` | `cameras`, `health_logs` | IMPLEMENTED BUT NOT VERIFIED |
| 14 | Camera Angle & Zones | `camera-setup` | `views/CameraSetupView.jsx` | `/api/cameras`, `/api/zones`, `/api/cameras/{id}/config` | `monitoring_zones`, `cameras` | IMPLEMENTED BUT NOT VERIFIED |
| 15 | Water Leakage | `leakage` | `views/LeakageView.jsx` | `/api/incidents/leakage`, `/api/models/model-water-leakage/evaluation`, `/api/incidents/leakage/{id}/review` | `leakage_incidents`, `detection_events`, `ai_models` | PARTIALLY IMPLEMENTED |
| 16 | Evidence Gallery | `evidence` | `views/EvidenceView.jsx` | `/api/evidence`, `/api/uploads/evidence`, `/api/evidence/{id}` | `evidence_files` | IMPLEMENTED BUT NOT VERIFIED |
| 17 | Plant & Pipelines | `plant` | `views/PlantView.jsx` | `/api/plant-locations`, `/api/pipelines` | `plant_locations`, `pipelines` | PARTIALLY IMPLEMENTED |
| 18 | AI Models & Datasets | `models` | `views/ModelsView.jsx` | `/api/models`, `/api/models/health`, `/api/models/{id}/versions`, `/api/models/{id}/evaluation`, `/api/datasets` | `ai_models`, `model_versions`, `dataset_metadata` | PARTIALLY IMPLEMENTED |
| 19 | Reports | `reports` | `views/ReportsView.jsx` | `/api/reports`, `/api/reports/{id}/download` | `reports` | IMPLEMENTED BUT NOT VERIFIED |
| 20 | Notifications | `notifications` | `views/NotificationsView.jsx` | `/api/notifications/status`, `/api/notifications/test`, `/api/notifications/deliveries` | `notification_deliveries` | IMPLEMENTED BUT NOT VERIFIED |
| 21 | System Health | `system-health` | `views/SystemHealthView.jsx` | `/api/system/health` | database, `ai_models`, notification config | FULLY IMPLEMENTED AND VERIFIED |
| 22 | Audit Logs | `audit-logs` | `views/AuditLogView.jsx` | `/api/audit-logs` | `audit_logs` | FULLY IMPLEMENTED AND VERIFIED |
| 23 | User Management | `users` | `views/UsersView.jsx` | `/api/users`, `/api/users/{id}` | `users` | IMPLEMENTED BUT NOT VERIFIED |
| 24 | Configuration | `config` | `views/ConfigurationView.jsx` | `/api/restricted-zones`, `/api/zones` | `restricted_zones`, `monitoring_zones` | PARTIALLY IMPLEMENTED |
| 25 | General Settings | `settings` | `views/SettingsView.jsx` | `/api/settings` | `app_settings` | IMPLEMENTED BUT NOT VERIFIED |

## 5. Detailed Findings by Section

### Operational / backend-backed pages

- **System Health**: `/api/system/health` returns live DB, camera, model, notification and storage status. Verified HTTP 200.
- **Audit Logs**: `/api/audit-logs` returns 13 real audit records. Verified HTTP 200.
- **AI Models & Datasets**: Model registry, version history and V3 evaluation are live. The page is partially implemented because dataset upload/registration UI is not present; dataset metadata is read-only.
- **Camera Management / Camera Angle & Zones**: Backend camera, start/stop/test/config, zone and stream endpoints exist. Not verified with a physical camera.
- **Water Leakage**: Backend incident and review APIs exist, and V3 evaluation data is exposed. No leakage incidents exist because no camera/event pipeline has run operationally.
- **Evidence / Reports / Notifications**: Backend APIs exist, but tables are empty and external notification providers are unconfigured.

### Partially implemented / UI-assisted pages

- **Overview**: Uses real stats/alerts/cameras, but also contains static schematic and fixed service labels.
- **Live Monitoring**: Uses client-side COCO-SSD for person/phone detection and posts alerts/observations to the backend. It does not run the V3 leakage model.
- **Pipe Inspection & Scanner**: Uses client-side `detectPipeLeakage` heuristics plus COCO-SSD. It is not the V3 leakage model.
- **Expected Personnel**: Has backend rules and WhatsApp endpoints, but defaults to a staged prototype simulator.
- **Personnel Schedule & Monitoring**: Has backend rules/events, but includes a staged simulator and client-side person detection.
- **Near Miss**: Manual distance slider and alert dispatch only; no automatic worker/vehicle distance tracking.
- **Incidents**: Manual observation logging and anomaly-check endpoint; no real incident review workflow.
- **Camera & AI Health**: Uses local browser stream metrics, not backend camera health.
- **Configuration**: Saves a fixed restricted-zone polygon; threshold sliders are not persisted.
- **Plant & Pipelines**: Backend CRUD is partially present; only seeded records exist and no edit/delete UI is exposed.

### UI-only

- **Packaging Inspection**: Clearly labeled `DEMO / SIMULATED`. No backend, camera, model, conveyor or reject actuator integration.

## 6. Water Leakage Model Audit

### Files verified

| Version | Path | Size |
|---|---|---:|
| V1 | `backend/models/model-water-leakage/v1/best.pt` | 6,450,666 bytes |
| V2 | `backend/models/model-water-leakage/v2/best.pt` | 6,452,074 bytes |
| V3 | `backend/models/model-water-leakage/v3/best.pt` | 6,453,994 bytes |

### Registry

- `ai_models.id = model-water-leakage`
- `status = AVAILABLE`
- `active_version = 3`
- `weights_path = model-water-leakage/v3/best.pt`
- `classes_json`:
  - `0`: Water Dripping
  - `1`: Water Spraying
  - `2`: Visible Water Flow
  - `3`: Pipe Joint Leakage
  - `4`: Water Accumulation
  - `5`: Normal/Dry Pipeline
- `model_versions`: v1, v2, v3 all `COMPLETED`.

### Live model check

`GET /api/models/model-water-leakage/health` returned HTTP 200 with `loaded: true`.  
`GET /api/models/model-water-leakage/evaluation` returned HTTP 200 with active version 3 and class-wise metrics.  
A live `POST /api/models/model-water-leakage/detect` request on a test image returned HTTP 200 with two `Pipe Joint Leakage` detections.

### Integration status

- V3 is registered, loadable and usable by `InferenceService`.
- It is **not connected to a live camera** because there are zero registered cameras.
- It is **not connected to the Live Monitoring page**. Live Monitoring uses client-side COCO-SSD.
- It is **not connected to the Pipe Inspection page**. That page uses client-side heuristics.
- The Water Leakage page displays model/evaluation data, but there are zero leakage incidents.
- Evidence and alert generation exist in the backend and were previously verified in software tests, but no operational camera/event records exist now.

### V3 test metrics

- Precision: 0.4760
- Recall: 0.2689
- mAP50: 0.1853
- mAP50-95: 0.1271
- Per-class mAP50:
  - Water Dripping: 0.0669
  - Water Spraying: 0.0226
  - Visible Water Flow: 0.0850
  - Pipe Joint Leakage: 0.2941
  - Water Accumulation: 0.5282
  - Normal/Dry Pipeline: 0.1151

These are real evaluation metrics from the registered test split. They are not production accuracy claims.

## 7. Missing / Incomplete Functionality

1. No physical CCTV camera or live RTSP stream is connected.
2. No trained model is connected to Live Monitoring or Pipe Inspection.
3. Packaging Inspection is a demo only.
4. Near Miss has no automatic distance measurement.
5. Incidents page is observation logging, not a full incident review workflow.
6. Camera & AI Health uses local browser metrics, not backend camera health.
7. Configuration thresholds are not persisted.
8. Dataset management is read-only; no dataset upload/register UI.
9. Notification providers other than in-app are unconfigured.
10. Evidence, reports, alerts and leakage incidents are empty.
11. ONVIF discovery is not implemented.
12. Browser-level navigation/console testing was not performed.
13. Production load and long-run camera stability are unverified.

## 8. Recommended Verification Steps for Incomplete Features

### Camera / RTSP

1. Open **Camera Management**.
2. Register an authorized RTSP URL.
3. Click **Test**.
4. Start the camera.
5. Verify `GET /api/cameras/{id}/frame.jpg` returns an image.
6. Verify the MJPEG stream in **Camera Angle & Zones**.

### Water Leakage end-to-end

1. Register and start a camera.
2. Draw a pipeline zone and assign `model-water-leakage`.
3. Process a genuine recorded leakage video or live stream.
4. Confirm `detection_events`, `leakage_incidents`, `alerts` and `evidence_files` are created.
5. Confirm incidents remain `POSSIBLE`/`SUSPECTED` until human review.

### Notifications

1. Set SMTP/SMS/Telegram/WhatsApp credentials in `backend/.env`.
2. Open **Notifications** and click **Test**.
3. Verify provider acceptance is recorded in `notification_deliveries`.
4. Confirm unconfigured providers remain `NOT_CONFIGURED`.

### Reports / Evidence

1. Generate a report from **Reports**.
2. Upload or generate evidence from **Evidence Gallery**.
3. Verify files are stored under the configured evidence/report directories.

### Browser-level QA

1. Open every sidebar section in a browser.
2. Check console/network errors.
3. Verify no simulated/demo data is presented as real operational data.
4. Re-run this audit after browser automation is available.

## 9. Major Feature Locations

| Major feature | Section |
|---|---|
| Live camera connection and RTSP | Camera Setup |
| Camera lifecycle and health | Camera Management |
| Camera regions and thresholds | Camera Angle & Zones |
| Water leakage incidents and review | Water Leakage |
| Model registry and evaluation | AI Models & Datasets |
| Evidence and uploads | Evidence Gallery |
| Alerts and acknowledgement | Safety Events |
| Personnel rules and events | Expected Personnel / Personnel Schedule & Monitoring |
| Reports and downloads | Reports |
| Notification providers | Notifications |
| System health | System Health |
| Audit trail | Audit Logs |
| Users and roles | User Management |
| General settings | General Settings |
| Packaging demo | Packaging Inspection |
| Client-side pipe heuristic | Pipe Inspection & Scanner |
| Client-side live monitoring | Live Monitoring |

## 10. Audit Conclusion

- The application is a real full-stack FastAPI + React application with a populated model registry and working backend APIs.
- The V3 water leakage model is real, trained, registered, loadable and independently evaluated.
- The model is **not** yet connected to live CCTV or the Live Monitoring page.
- Several older industrial-safety pages remain partially implemented or demo-only.
- The operational database has no cameras, alerts, incidents, evidence or reports, so many pages will correctly show empty states.
- This audit did not modify code or data.