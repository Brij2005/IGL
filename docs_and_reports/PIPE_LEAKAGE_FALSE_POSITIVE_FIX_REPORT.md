# Pipe Leakage False-Positive Fix Report

Date: 2026-11-23
Component: Pipeline Inspection / Water Leakage visual detection (frontend)

---

## 1. Root cause

The `VISIBLE PIPE LEAKAGE DETECTED` alert was **not** produced by a trained AI model. It was
produced by a **client-side pixel heuristic** in
`frontend/src/services/realVisionProcessor.js` → `detectPipeLeakage()`.

The original decision chain was:

```js
// rule: any bright, high-gradient, neutral-grey pixel = "water spray"
const isWaterSprayPixel =
  lum > 135 && gradMag > 20 && Math.abs(r - g) < 35 && Math.abs(g - b) < 35;

// rule: 1.2% of the frame + 3 cells + density 10
const frameHasSprayPattern =
  sprayRatio >= 0.012 && activeSprayCells >= 3 && maxCellDensity >= 10;

// uploaded images skipped even the temporal check
const isConfirmedLeakage = source === "LIVE_CAMERA"
  ? (frameHasSprayPattern && consecutiveFrames >= 4)
  : frameHasSprayPattern;

// confidence was fabricated, not measured
let rawConfidence = 0.84 + Math.min(0.12, ...);
confidence: Math.min(0.96, Math.max(0.85, rawConfidence))
```

Four defects combined to produce the false alert:

| # | Defect | Why it caused the bug |
|---|---|---|
| 1 | **No pipe detection at all** | The function never checked whether a pipe existed. Any scene could qualify. |
| 2 | **Over-broad pixel rule** | "Bright + high local gradient + neutral colour" matches bright walls, windows, cupboard edges, faces and clothing. |
| 3 | **Fabricated confidence** | `Math.max(0.85, ...)` guaranteed a 0.85–0.96 "confidence" for every positive, so the UI's `confidence >= 0.75` gate was *always* true. |
| 4 | **Confirmed wording** | The view rendered `🔴 VISIBLE PIPE LEAKAGE DETECTED` with `severity: HIGH` and `event_type: VISIBLE_PIPE_LEAKAGE`. |

**The alert text was traced to source:** `PipeInspectionView.jsx:307/572/633` and
`ImageCameraInspectionView.jsx:293/565/633`. The camera name `Webcam Input (HD)` in the
alert matches the frontend's `zone_name` for the pipe scanner, confirming the frontend was the
source (not the backend event engine).

**Evidence of the bug in the live database:** two `HIGH` severity alerts with
`camera_id = 'Webcam Input (HD)'` / `'CAM-LIVE'`, both `ACKNOWLEDGED`, remain in `alerts`.
They were left in place as the audit trail (not deleted).

---

## 2. Incorrect model prediction / class

**There was no model prediction.** No classifier, no class name, no bounding box from a trained
network was involved.

| Item | Before | After |
|---|---|---|
| Detector type | pixel heuristic | pixel heuristic **+ mandatory pipe-context gate** |
| Class used | hard-coded string `VISIBLE_PIPE_LEAKAGE` | `POSSIBLE_PIPE_LEAKAGE` (review) — never "confirmed" |
| Confidence | fabricated 0.85–0.96 | computed from evidence, **capped at 0.75** |
| Pipe required | no | **yes** — `detectPipeContext()` must confirm a pipe |

The trained Water Leakage model (`model-water-leakage`, v3, AVAILABLE) was **not** involved and
was **not** modified. It remains the authoritative trained path on the backend.

---

## 3. Files changed

| File | Change |
|---|---|
| `frontend/src/services/realVisionProcessor.js` | Added `detectPipeContext()` (pipe localisation). Rewrote the leakage decision to require pipe context, restrict analysis to the pipe region + margin, distinguish droplets from smooth highlights via local contrast, and compute a capped confidence. Added `diagnostics` to the result. |
| `frontend/src/views/PipeInspectionView.jsx` | Alert dispatch gated on pipe context; event type `POSSIBLE_PIPE_LEAKAGE_REVIEW` at `MEDIUM`; new `PIPE NOT VISIBLE – CHECK CAMERA POSITION` banner; review-required wording; status card shows pipe-visible / possible-leak separately. |
| `frontend/src/views/ImageCameraInspectionView.jsx` | Same fixes as above (duplicate implementation). |
| `frontend/tests/pipe_leakage_regression.mjs` | **New** — 16 deterministic rule-validation tests covering all 10 required scenarios. |

No backend file was changed. No model weights, datasets, database records or other modules were
touched.

---

## 4. New validation logic

### 4.1 `detectPipeContext(sourceElem, options)` — pipe localisation

A deterministic structural test for a cylindrical pipe under directional light:

1. Downscale to 320 px wide, compute per-pixel luminance and gradient magnitude.
2. Build per-row and per-column **edge support** and **mean luminance** profiles.
3. Search for two long, roughly parallel high-gradient edges (row-pair for a horizontal pipe,
   column-pair for a vertical one) separated by 8–110 px.
4. Require a **brighter band between the edges** (cylindrical highlight) —
   `insideLum / outsideLum >= 1.06`.
5. Accept only when `edgeSupport >= 0.42` and combined score `>= 0.86`.
6. Returns `{ pipeVisible, confidence, region: [x,y,w,h], axis, reason, signals }`.
   Region is normalised so the caller can analyse only that band.
7. An optional operator ROI (`options.region`) restricts the search and is validated for size.

### 4.2 Gated leakage decision

```
quality check  →  pipe context  →  spray analysis inside pipe region + margin  →  verdict
```

* **No pipe** → `status: "NO_PIPE_DETECTED"`, `detected: false`, `confidence: 0`,
  `evidence_region: null`, operator message `PIPE NOT VISIBLE – CHECK CAMERA POSITION`.
* **Pipe, no spray** → `status: "NO_LEAKAGE"`, `pipe_detected: true`.
* **Pipe + concentrated spray** → `detected: true`, `status: "REVIEW_REQUIRED"`.

### 4.3 Spray analysis restricted to the pipe region (+ margin)

Spray pixels are counted only within the detected pipe band plus a margin
(`max(24px, band × 1.5)`), so bright areas elsewhere (clothing, walls, furniture) can never be
attributed to the pipe. The margin exists because a real jet escapes *from* the pipe surface.

### 4.4 Droplets vs. smooth highlights

A new **local-contrast** test rejects smooth bright regions:

```js
// a droplet is a small bright feature on a darker background
if (localBrightRatio >= 0.6) continue;   // smooth highlight / lit surface
```

This is what stops a **clean glossy pipe** (specular highlight) from being reported as leaking —
a failure mode found and fixed while writing the tests.

### 4.5 Concentration-based decision (not whole-frame coverage)

```js
const MIN_SPRAY_RATIO = 0.002;   // sanity floor only
const MIN_ACTIVE_CELLS = 3;      // clustered
const MIN_PEAK_DENSITY = 12;     // concentrated
```

A leak jet is a *concentrated* cluster, so the decision is driven by concentration rather than by
the fraction of the search band covered. The confidence threshold was **not** simply raised.

### 4.6 Honest confidence

```js
rawConfidence = 0.35
  + 0.25 * spraySignal      // measured spray ratio
  + 0.20 * clusterSignal    // measured active cells
  + 0.15 * pipeSignal;      // measured pipe confidence
confidence = clamp(rawConfidence, 0.35, 0.75)
confidence_source = "PIXEL_HEURISTIC_NOT_A_TRAINED_MODEL"
```

### 4.7 Frontend presentation

* Alert is `POSSIBLE_PIPE_LEAKAGE_REVIEW`, severity `MEDIUM`, status `REVIEW_REQUIRED`.
* Headline `⚠️ POSSIBLE PIPE LEAKAGE — HUMAN REVIEW REQUIRED`.
* Explicit line: *"Not a trained-model detection… must not be treated as a confirmed defect."*
* New amber banner `🟠 PIPE NOT VISIBLE – CHECK CAMERA POSITION` with the underlying reason.
* Status card now shows `Pipe visible` and `Possible leak` as separate fields.

---

## 5. Regression test results

`node frontend/tests/pipe_leakage_regression.mjs` → **16 passed, 0 failed**

These are **deterministic rule-validation tests** (canvas shim + synthetic pixel fixtures),
explicitly *not* model-inference tests. They load the real functions from source.

| # | Required scenario | Result |
|---|---|---|
| 1 | Person sitting in an ordinary room → NO pipe leakage alert | PASS |
| 2 | Cupboard / furniture scene → NO pipe defect alert | PASS |
| 3 | Wall-only scene → NO pipe leakage alert | PASS |
| 4 | Person wearing dark clothing → NO pipe defect alert | PASS |
| 5 | Actual pipe with no visible leakage → NO leakage alert (pipe still recognised) | PASS |
| 6 | Actual pipe with a supported spray pattern → valid review-level detection | PASS |
| 7 | Confidence is computed and capped (no fabricated 0.85+) | PASS |
| 8 | No pipe detected → `NO_PIPE_DETECTED` or review status | PASS |
| 9 | Model/source unavailable → clear unavailable response, no fake detection | PASS |
| 10 | Evidence from one camera must not be attached to another camera's event | PASS (no evidence region is produced without pipe context) |

Additional: plain wall / cupboard / room are not misidentified as pipes; a too-small operator ROI
is rejected safely; `detectPipeContext(null)` returns a reason instead of throwing.

---

## 6. Actual model test results

**No model was trained or modified in this fix, so no model metrics are claimed.**

| Check | Result |
|---|---|
| Backend suite `pytest -q` | **136 passed, 0 failed** |
| Frontend production build | **PASSED** (2780 modules) |
| Frontend regression suite | **16 passed, 0 failed** |
| Exact false-positive E2E (real source + rendered room/cupboard/wall images) | **10 passed, 0 failed** |
| Live dev server serves fixed code | verified (`detectPipeContext` present, fabricated `0.84` gone, new banner present) |

### Exact reported scenario (end-to-end)

Rendered the reported scene (person in a room with a cupboard) and ran the **real**
`detectPipeLeakage()` from source against its actual pixels:

```
[person in room with cupboard]
  detected      = False
  status        = NO_PIPE_DETECTED
  pipe_detected = False
  confidence    = 0
  message       = PIPE NOT VISIBLE – CHECK CAMERA POSITION
```

Identical results for the cupboard-only and wall-only scenes. The test created **no new alerts**
(2 pre-existing alerts before, 2 after).

The trained backend Water Leakage model remains available and unchanged; it was not exercised for
this fix because the reported alert did not originate there.

---

## 7. Remaining limitations

1. **The pipe detector is a heuristic, not a trained model.** It finds cylindrical *structure*
   (parallel edges + highlight band). Unusual pipe shapes (elbow-only views, heavily occluded
   pipes, dark scenes) may report `NO_PIPE_DETECTED`. That is a deliberate, safe failure mode:
   the system asks the operator to reposition rather than guessing.
2. **The leakage decision remains heuristic.** All positives are capped at 0.75 and labelled
   `REVIEW_REQUIRED`. For genuine confirmed detection, the trained backend
   `model-water-leakage` must be used.
3. **Pipe defects / corrosion are not detected client-side at all.** No such claim is made; those
   require a trained model (the corrosion model is `NOT_TRAINED`).
4. **No hardware validation.** No physical camera or thermal device was available; live-camera
   behaviour was verified only through the shared code path and synthetic frames.
5. **Two pre-existing `HIGH` alerts remain in the database** as the audit trail of the original
   bug. They were not deleted. An operator can resolve them via the UI.
6. **Duplicate implementations remain** (`PipeInspectionView` and
    `ImageCameraInspectionView`). Both were fixed identically; consolidating them is future work.
7. **A true fix would be a trained pipe/leak model.** This change removes the *false* claims and
   makes the heuristic honest and pipe-gated; it does not turn the heuristic into a classifier.

---

## 8. Verification summary

| Requirement | Status |
|---|---|
| Pipe leakage only with actual visual evidence | Met (pipe context mandatory) |
| Pipe defects only when a pipe + defect are identified | Met (no client-side defect claim at all) |
| Ordinary scenes produce no pipe alerts | Met (scenarios 1–4) |
| Evidence supports the alert | Met (no evidence region without pipe context) |
| Existing valid detections and modules keep working | Met (136 backend tests, build) |
| Not fixed by raising the confidence threshold | Met (rule redesigned; confidence recomputed) |
| No frontend-only masking | Met (detection logic changed, not just the label) |