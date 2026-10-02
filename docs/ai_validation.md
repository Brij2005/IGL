# AI Validation Status

## Current Result

- Model weights: **NOT_CONFIGURED**. No weights are present in the workspace and none were downloaded. `python scripts/validate_model.py` returned `overall_state=MODEL_NOT_CONFIGURED`, `validation_state=NOT_VALIDATED`, every check `NOT_RUN`, `detection_quality_validated=false`, exit code 2. Local model discovery found 0 candidate checkpoints.
- Webcam acquisition: **REAL-WORLD VALIDATED** for capture only. `GET /api/v1/cameras/webcam/devices` found 6 probe indices with device 0 reporting 640x480. Starting the registered "Laptop Webcam" opened the physical USB camera through the DSHOW backend, delivered 165 real frames, and health reported `camera_state=CAMERA_AVAILABLE`, `measured_performance=MEASURED_FROM_OBSERVED_FRAMES`, `camera_fps=8.03`. Reopening returned to `RUNNING` with 67 frames. Camera acquisition validates the reader, buffer and lifecycle; it does not validate model inference or detector performance, and no inference ran.
- Detection and tracking performance: **NOT_VALIDATED / BLOCKED_BY_EXTERNAL_DEPENDENCY**. `GET /api/v1/workers/tracks` returned 0 records. Real-time detection, inference FPS, accuracy, precision, recall, F1 and tracking accuracy have not been measured, because no model has ever executed on this machine.
- Safety events, incidents and live alarms: **NOT_CONFIGURED / BLOCKED_BY_EXTERNAL_DEPENDENCY**. A confirmed event requires a real detection, and no model is loaded. `GET /api/v1/alarms` returned 0 rows and `GET /api/v1/alarms/policy` reported 0 alarms.
- Detector capability: **IMPLEMENTED but not operational** for `RESTRICTED_ZONE`, `FIRE`, `SMOKE`, `PERSON` and `PHONE` (reported `IMPLEMENTED` / `MODEL_NOT_CONFIGURED`). **NOT_IMPLEMENTED** for `PPE`, `HELMET`, `SAFETY_VEST`, `PROXIMITY`, `FALL`, `LEAKAGE` and `UNSAFE_BEHAVIOR` (reported `NOT_IMPLEMENTED` / `NOT_AVAILABLE`). `operational_detector_count` was 0.
- Notification delivery: **NOT_CONFIGURED**. Test sends returned `EMAIL_NOT_CONFIGURED` and `WHATSAPP_NOT_CONFIGURED`. No message has ever been delivered from this deployment.
- Physical alarm: **NOT_CONFIGURED**. `POST /api/v1/alarms/physical/test` returned `PHYSICAL_ALARM_NOT_CONFIGURED` with `hardware_verified=false`. No actuator is connected.
- IGL validation: **NOT_CONFIGURED / NOT_VALIDATED**. No authorised IGL footage, plant layout, SOP or labelled dataset has been supplied or evaluated.
- Model-backed real-input runner: **BLOCKED_BY_EXTERNAL_DEPENDENCY**. `scripts/run_inference.py` is implemented and tested against controlled fixtures, but no compatible weights or authorised labelled IGL evaluation data are available.
- Production deployment: **NOT_VALIDATED**. TLS termination, reverse proxy, PostgreSQL topology and secrets-manager deployment are documented in [deployment.md](deployment.md) and were not exercised.

No authorised IGL camera footage, plant layout, PPE SOP dataset, model checkpoint, or labelled IGL validation dataset is present. The laptop webcam is a real source but is not authorised IGL validation footage. No substitute or synthetic data has been created to stand in for any of these.

## Why Nothing Is Claimed

The model adapter reports configuration, load and inference errors and does not create placeholder results. `health()` reports `inference_fps` as `null` until at least two real predictions complete, and `validation_state` as `NOT_VALIDATED` unconditionally, because loading and timing a model is not validating it. Tests exercise unavailable/invalid model states and component behaviour; a clean test run is recorded as `TEST_FIXTURE_EXECUTION`, which is a software test result and not a model-accuracy measurement.

`scripts/validate_model.py` labels its blank-frame throughput probe `SYNTHETIC_PROBE_NOT_REAL_FOOTAGE` and states in its own report that the run "does not speak to detection accuracy". A checkpoint whose runtime is not installed is reported as `MODEL_RUNTIME_DEPENDENCY_MISSING` with the missing distribution named, rather than being made to look loadable. `.onnx` and `.engine` formats therefore cannot be used here: `onnxruntime` and `tensorrt` are not installed.

## What The Automated Suite Does Prove

`python -m pytest -q` from `backend/` returned 365 passed, 0 failed. Among those, 27 tests cover the alarm subsystem (policy, cooldown, repeat window, state transitions with reasons, expiry, physical state vocabulary) and the remaining suites cover the safety primitives, camera ingestion including the reopen states and the operator-stop marker, health semantics, notification templates, model validation, migrations, and access control. These are software results. None of them is a detection, delivery, alarm actuation or IGL validation result.

## Track Records

Persisted visual tracks and their latest real detections are available through `GET /api/v1/workers/tracks`; recent-only results use the stored `last_seen_at` timestamp window. The endpoint intentionally does not associate tracks with employee accounts. A track record does not establish a current camera view. `helmet_status`, `ppe_status` and `phone_status` are always `UNKNOWN`, because no implemented detector in this build can produce an absence or compliance verdict. The Workers view joins linked incidents from `GET /api/v1/events` `incident_ids` and states that an empty track list does not indicate the area is safe. On this machine the endpoint returned 0 records.

## Next Validation Gate

Before reporting model performance:

1. Authorise and supply a compatible checkpoint. Record its path, the SHA-256 the platform reports, `MODEL_NAME`, `MODEL_VERSION`, `MODEL_CONFIDENCE_THRESHOLD` and `MODEL_DEVICE`.
2. Run `python scripts/validate_model.py --weights <path>` and record each check. A `PASS` set proves the artifact loads, executes and returns a well-formed result. It says nothing about accuracy.
3. Configure the detectors the model actually supports, with operator-set thresholds and zone geometry, and record which thresholds remain `ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION`.
4. Authorise and provide representative video and frame-level labels, document camera conditions, and run the actual pipeline.
5. Calculate precision, recall, F1, false-positive and false-negative rates, latency and FPS from measured outputs. Keep the resulting report explicitly scoped to that dataset and setup.
6. For IGL validation, additionally require approved IGL SOPs, zone geometry with a documented coordinate reference, escalation owners, retention rules, and a reviewed independent evaluation.

Until those steps are completed for the specific dataset and hardware, detection quality remains `NOT_VALIDATED` and `detection_quality_validated` stays `false`.
