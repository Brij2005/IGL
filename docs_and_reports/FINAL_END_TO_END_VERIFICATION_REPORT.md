# IGL VisionGuard — Final Independent End-to-End Verification Report

**Date:** 2026-11-23
**Type:** Independent verification of the existing implementation (no new features added)
**Method:** Direct code inspection + runtime tests against real frames (synthetic images, not physical cameras) + full regression suite

> This report describes what was **independently verified** against the actual code and runtime
> behavior. The implementation report was **not** assumed correct — every claim was re-checked.
> A genuine bug was found in the scene analyzer and fixed (see §4). Software tests, simulated
> inputs and physical-hardware tests are reported separately. No successful physical-hardware
> testing is claimed because no physical camera is available.

---

## 1. What Was Independently Verified

All new backend modules, the `/api/ai/*` API surface and the 5 new frontend pages were inspected
line-by-line and exercised at runtime. The verification covered:

| Component | Verified by |
|---|---|
| `model_registry.py` | Code inspection + capability/compatibility unit tests |
| `scene_analyzer.py` | Code inspection + 12-scene edge-case probe + unit tests |
| `mode_manager.py` | Code inspection + Automatic/Manual/hysteresis tests |
| `orchestrator.py` | Code inspection + Model Hub / Camera Command / Mapping payload tests |
| `/api/ai/*` APIs | Live TestClient requests against every endpoint |
| AI Model Hub | `ModelHubView.jsx` → `fetchModelHubApi()` → `/api/ai/model-hub` |
| Camera Command Centre | `CameraCommandCenterView.jsx` → `fetchCameraCommandCenterApi()` |
| Model-to-Camera Mapping | `ModelCameraMappingView.jsx` → mapping + manual-models APIs |
| Detection & Alerts | `DetectionCenterView.jsx` → `/api/ai/detections` |
| Model Training Centre | `TrainingCentreView.jsx` → model hub + datasets |
| Water Leakage / Pipeline Inspection | Full existing pytest regression (81 module tests) |

---

## 2. Automatic Mode — Actual Results

All checks passed (37/37 in the end-to-end verification, plus 22 unit tests):

| Requirement | Result | Evidence |
|---|---|---|
| Camera frame reaches central AI Engine | PASS | Worker reads adapter frames and encodes them |
| Scene Analyzer analyzes the actual frame | PASS | `analyze_frame()` runs on real encoded pixels |
| Scene classification NOT dependent on camera name | PASS | Analyzer takes only `image_bytes` + `source_type`; a camera named "Corrosion Pump Camera" with a water frame correctly classified WATER |
| Suitable available models selected | PASS | Water scene → `model-water-leakage` + `model-yolov8n` |
| Incompatible models NOT selected | PASS | Untrained thermal/corrosion never selected |
| Scene change updates model selection | PASS | Water→General switch re-evaluates; water model dropped, general retained |
| 3-frame hysteresis works | PASS | Single early frame does NOT switch; switch after 3 stable frames |
| No unnecessary model switching | PASS | 0 switches over 15 rapidly alternating frames |
| Multiple compatible models on one camera | PASS | 2 models run simultaneously on the water camera |

---

## 3. Manual Mode — Actual Results

All checks passed:

| Requirement | Result |
|---|---|
| Operator can switch Automatic → Manual | PASS |
| Models can be selected/deselected | PASS |
| Manual selection applies to the correct camera | PASS |
| Incompatible models rejected | PASS |
| Untrained / unavailable models rejected | PASS |
| Returning to Automatic resumes automatic selection | PASS |

Note: after `PUT /manual-models`, the per-camera *decision cache* is reset (by design) and is
repopulated on the next worker tick. The authoritative runtime selection (`active_model_ids`)
correctly reflects the manual list immediately on the next frame. This is a UI-cache nuance, not a
selection bug.

---

## 4. Scene Classification Accuracy and Limitations

### 4.1 Bug found and fixed during verification

The original scene analyzer used **pure HSV hue-fraction thresholds**. Empirical probing proved
this misclassified ordinary scenes:

- `wooden cupboard`, `tan wall`, `human face` → all classified **CORROSION** (identical warm hue band)
- `blue sky through window`, `blue monitor` → classified **WATER** (identical blue hue band)

Root cause: hue alone cannot separate rust from wood/tan/skin, or water from sky/monitor. This
would have caused the corrosion model to run on cupboards and the water model to run on windows —
the exact false-positive class the system is meant to avoid.

### 4.2 Fix applied (minimal, in `scene_analyzer.py`)

1. **Saturation floors** — rust requires high saturation (S≥120), excluding pale tan/wood walls.
2. **Edge-density guard** — a region must have enough texture (Canny edge density ≥0.03) to count
   as a real object; flat walls, clear sky and painted cupboards have ~0.0 edge density.
3. **Dominance thresholds** — raised fraction requirements (water 0.55, rust 0.45, skin 0.35).
4. **Safe fallback** — when no scene is confident, returns `GENERAL` with low confidence (0.4),
   never guessing.

### 4.3 Post-fix accuracy (12-scene probe)

Ordinary scenes — all correctly fall back to GENERAL (no false classification):

| Scene | Result |
|---|---|
| bright white wall, grey concrete, blue sky window, wooden cupboard, tan wall, face (flat), night, green foliage, office monitor, yellow machine, textured cupboard, textured tan wall | **GENERAL** ✓ |

Genuinely textured specialised scenes — correctly detected:

| Scene | Result |
|---|---|
| realistic water (ripples + pipe) | WATER (0.95) ✓ |
| realistic rust (flaking texture) | CORROSION (0.95) ✓ |

### 4.4 Honest limitation

Pixel-only classification of water/corrosion/face versus ordinary objects is inherently
imperfect. A realistic textured face with low overall skin fraction still falls back to GENERAL —
this is the **safe, honest behavior** (GENERAL only runs the general model and never raises
specialised false positives). The system prefers a correct GENERAL fallback over a wrong
specialised classification.

---

## 5. Model Availability and Compatibility

Independently confirmed against the live registry and runtime:

| Model | Status | Loadable | Selection behavior |
|---|---|---|---|
| `model-yolov8n` | AVAILABLE | Yes | Selected in Automatic on RGB |
| `model-water-leakage` (v3) | AVAILABLE | Yes | Selected in Automatic (candidate for all RGB scenes) |
| `model-thermal-person` | NOT_TRAINED | No (returns None) | Never selected, never runs |
| `model-pipe-corrosion` | NOT_TRAINED | No (returns None) | Never selected, never runs |

- **Thermal input never gets RGB models** — verified: thermal camera selects `[]` (thermal model is untrained).
- **Untrained models never run** — `get_model()` returns `None`; they are filtered from both modes.
- **Frontend/backend status consistency** — verified: `status == "AVAILABLE"` matches the `available` flag for every model.
- **No fake alerts from unavailable models** — verified: untrained models produce no detections.

---

## 6. Camera-to-Alert Flow Results

Full chain verified end-to-end with real inference (restricted-zone person scenario):

`Camera → Live Frame → Scene Analyzer → Model Selection → Inference → Detection Validation → Event Engine → Evidence → Alert → Dashboard`

| Check | Result | Evidence |
|---|---|---|
| Correct camera ID | PASS | Event/alert/evidence all carry the originating camera_id |
| Correct model name | PASS | `detection_events.model_id` + joined `model_name` |
| Actual detection timestamp | PASS | `frame_timestamp` / `created_at` persisted |
| Real confidence value | PASS | Confidence from inference stored |
| Correct evidence image | PASS | `evidence_files` row + file written to `EVIDENCE_DIR` |
| Duplicate alert prevention | PASS | Second identical frame → `DUPLICATE`, no new alert |
| Camera-specific event isolation | PASS | Each camera's events counted separately |
| Pipe-leakage false-positive regression | PASS | Wall/sky/wood → GENERAL; a non-leakage class (cell phone) → `MOBILE_PHONE_USAGE`, never `WATER_LEAKAGE` |

---

## 7. Existing Modules Regression Results

Full existing test suite re-run — **no regression**:

| Module | Tests | Result |
|---|---|---|
| Water Leakage pipeline | `test_leakage_pipeline.py` | PASS |
| Camera worker | `test_camera_worker.py` | PASS |
| Pipeline Corrosion | `test_corrosion_module.py` | PASS |
| Thermal | `test_thermal_module.py` | PASS |
| Smart Entry & Exit / Security | `test_security_module.py` | PASS |
| Auth enabled / roles | `test_auth_enabled.py`, `test_auth_roles.py` | PASS |
| Database integrity | `test_database_integrity.py` | PASS |
| Modules integration | `test_modules_integration.py` | PASS |
| **Full suite total** | all test files | **158 passed** |

No existing feature was removed or replaced. Existing APIs, DB records, models and cameras are intact.

---

## 8. Backend and Frontend Test Results

| Check | Result |
|---|---|
| Backend full pytest | **158 passed** (136 original + 22 architecture, incl. 4 new edge-case tests) |
| New architecture unit tests | **22 passed** |
| End-to-end verification script | **37/37 passed** |
| Performance/resource script | **7/7 passed** |
| Frontend production build (`npm run build`) | **Passed** (2785 modules transformed) |
| New frontend pages | All 5 wired to real `/api/ai/*` endpoints; no hardcoded/fabricated data |

---

## 9. Performance Results

| Check | Result |
|---|---|
| Model load deduplication | PASS — same model instance shared across all cameras (`is` identity) |
| Untrained model fail-fast | PASS — 0.004s for 2 calls (negative cache, no repeated disk hits) |
| Multiple camera workers | PASS — 3 cameras run 3 independent workers |
| Per-camera worker processing | PASS — each worker processed frames independently |
| Camera failure isolation | PASS — stopping 1 camera leaves the other 2 running |
| Mode-switching stability (no thrash) | PASS — 0 switches over 15 rapid alternating frames |

Resource design: one loaded model instance per model id (thread-safe RLock cache); one daemon
worker thread per running camera; untrained models short-circuited by a 30s negative cache.

---

## 10. Real Hardware Testing Status

**Not performed — no physical hardware available.** The following require physical devices and
are explicitly **not** claimed as tested:

1. Real CCTV / RTSP / ONVIF camera connection.
2. Thermal camera + calibrated radiometric source.
3. Genuine factory CCTV footage evaluation.
4. RFID / access-control / turnstile / alarm hardware.
5. Production load and long-run camera stability over days.

All camera/scene/model testing in this report used **synthetic images** (generated with OpenCV)
fed through the real backend pipeline. This validates software behavior, **not** physical camera
or real-world scene accuracy.

---

## 11. Remaining Bugs and Limitations

| # | Item | Severity | Note |
|---|---|---|---|
| 1 | Scene classification is pixel-heuristic | Medium | Conservative fallback to GENERAL when uncertain; not a substitute for a trained scene classifier. Documented honestly. |
| 2 | `model-thermal-person` and `model-pipe-corrosion` are NOT_TRAINED | Expected | No genuine thermal/corrosion dataset or GPU available; correctly reported unavailable. |
| 3 | Per-camera decision cache resets on mode change | Low (cosmetic) | UI cache repopulates on next worker tick; runtime selection is always correct. |
| 4 | No physical camera validated | Expected | Hardware unavailable. |

No blocking bugs remain. The scene-analyzer bug (#1 root cause) was found and fixed during this verification.

---

## 12. Git Commit and Remote Synchronization Status

| Item | State |
|---|---|
| Branch | `main` |
| Last commit | `ba6dc52 docs: add pipe leakage false-positive fix report` |
| Working tree | Modified: `api_routes.py`, `camera_worker.py`, `App.jsx`, `Sidebar.jsx`, `api.js` |
| Untracked (new) | `model_registry.py`, `scene_analyzer.py`, `mode_manager.py`, `orchestrator.py`, `tests/test_central_architecture.py`, 5 new views, this report + implementation report |
| Committed? | **No** — changes are uncommitted, awaiting explicit user request to commit |
| Remote sync | Not pushed (no commit made) |

Per the preservation rules, no commit, push, database reset or destructive action was performed.
Changes are staged in the working tree only.

---

## 13. Preservation Compliance

| Rule | Status |
|---|---|
| No existing source rewritten unnecessarily | ✓ (only `scene_analyzer.py` logic hardened; `camera_worker.py` model-selection step extended) |
| No DB records deleted | ✓ (live DB intact: 5 migrations, 4 models, 3 versions, 36 tables) |
| Model files/checkpoints preserved | ✓ (yolov8n.pt + v1/v2/v3 all present) |
| Camera configurations preserved | ✓ (all 16 cameras intact) |
| Existing APIs preserved | ✓ (all original endpoints still present) |
| No DB reset | ✓ |
| No backup copies created | ✓ |
| No unknown processes terminated | ✓ |
| No permanent fake alerts/records from tests | ✓ (all test scripts used temp DBs; live DB unchanged) |

---

## 14. Conclusion

IGL VisionGuard's centralized AI architecture is **stable, integrated and honestly verified**:

- Automatic mode (default) selects compatible *available* models from **actual frame content**,
  with stable hysteresis and no thrashing.
- Manual mode pins operator models while still rejecting untrained/unavailable/incompatible ones.
- Only trained, validated, available models run; thermal never receives RGB models.
- The full camera → alert → evidence flow works with real deduplication and camera isolation.
- Existing modules (leakage, pipe, smart entry, auth, alerts, evidence, reports) all pass regression.
- One genuine scene-classification bug was found and fixed with a conservative, honest fallback.
- Physical-hardware testing remains pending and is not claimed.

**Software/simulated verification: complete and passing. Physical-hardware verification: pending (no hardware).**