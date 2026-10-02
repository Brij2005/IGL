# Validation Record

This file records what was actually run on this Windows laptop and what it does and does not prove. Nothing here is extrapolated, and no result is rounded up.

Status vocabulary: `IMPLEMENTED`, `TESTED` (automated), `REAL-WORLD VALIDATED` (observed in a real run here), `NOT_CONFIGURED`, `NOT_VALIDATED`, `BLOCKED_BY_EXTERNAL_DEPENDENCY`.

## Summary Table

| Check | Result | Status |
| --- | --- | --- |
| Automated test suite | 365 passed, 0 failed | TESTED |
| Static checks | compileall, `node --check` x2, four PowerShell scripts, `git diff --check` all clean | TESTED |
| Webcam capture | 6 probe indices, device 0 at 640x480, DSHOW, 165 real frames, camera_fps 8.03 | REAL-WORLD VALIDATED (capture only) |
| Operator-stop persistence | camera stayed stopped with 0 frames at +8 s, +18 s, +28 s | REAL-WORLD VALIDATED |
| Camera reopen | back to RUNNING with 67 frames through DSHOW; final stop NOT_STARTED | REAL-WORLD VALIDATED |
| Browser render | 11 views at 390/768/1024/1440/1920 px, zero overflow, zero console errors, zero failed requests | REAL-WORLD VALIDATED |
| Browser camera cycle | start/stop/reopen/stop reported VALIDATED through the real UI | REAL-WORLD VALIDATED |
| HTTP smoke | PASS: dashboard HTML, navigation, JS/CSS, health, workers API | REAL-WORLD VALIDATED |
| Worker tracks | 0 records returned | REAL-WORLD VALIDATED (empty) |
| Model validation CLI | `MODEL_NOT_CONFIGURED`, all checks `NOT_RUN`, exit 2 | NOT_CONFIGURED |
| Local model discovery | 0 candidate checkpoints | NOT_CONFIGURED |
| Detection / tracking | no weights, so nothing ran | BLOCKED_BY_EXTERNAL_DEPENDENCY |
| Safety event, incident | no confirmed event has ever existed here | BLOCKED_BY_EXTERNAL_DEPENDENCY |
| Live alarm | `GET /alarms` returned 0 rows; policy reported 0 alarms | NOT_CONFIGURED |
| Physical alarm | `PHYSICAL_ALARM_NOT_CONFIGURED`, `hardware_verified=false` | NOT_CONFIGURED |
| Email | `EMAIL_NOT_CONFIGURED` | NOT_CONFIGURED |
| WhatsApp | `WHATSAPP_NOT_CONFIGURED` | NOT_CONFIGURED |
| IGL plant validation | no IGL footage, layout, SOP or labelled data | NOT_CONFIGURED / NOT_VALIDATED |
| Production deployment | documented, not exercised | NOT_VALIDATED |

## Automated Suite

```powershell
cd backend
python -m pytest -q
```

Observed: `365 passed, 1 warning in 82.68s (0:01:22)`, 0 failed. Before this work the suite was 228 tests. The single warning is a Starlette deprecation notice about the `httpx` test client, emitted by the installed dependency and not by this code.

The suite migrates a temporary database with the real Alembic chain and never touches the operator database. 27 of those tests are `backend/tests/test_alarm_subsystem.py`, which cover alarm policy evaluation, cooldown and repeat-window suppression, state transitions with reason enforcement, acknowledgement, escalation, clearing, expiry, the audited history, and the physical state vocabulary. Those 27 tests are the evidence for alarm behaviour. They are not a live alarm.

## Static Checks

```powershell
python -m compileall -q backend/app ai_models scripts
node --check frontend/js/app.js
node --check scripts/smoke_browser.mjs
```

All four `scripts/*.ps1` files parse without errors. `git diff --check` reports no whitespace errors. These results are reproducible on demand.

## Webcam (Real Device)

`GET /api/v1/cameras/webcam/devices` found 6 probe indices, with device 0 reporting 640x480.

Starting the registered "Laptop Webcam" opened the physical USB camera through the DSHOW backend and delivered 165 real frames. The health layer reported:

- `camera_state=CAMERA_AVAILABLE`
- `measured_performance=MEASURED_FROM_OBSERVED_FRAMES`
- `camera_fps=8.03`

Stop returned `NOT_STARTED`. The camera was then checked at +8 s, +18 s and +28 s; the health worker interval is 5 s. It stayed stopped with 0 frames throughout, and health correctly dropped to `CAMERA_CONFIGURED_NOT_OBSERVED` rather than reporting a failure for a camera an operator had deliberately stopped. Reopening returned to `RUNNING` with 67 frames through DSHOW. A final stop returned `NOT_STARTED`. `reconnect_state=IDLE` throughout, which is correct because nothing needed to be retried.

This validates frame capture, the bounded frame buffer, the reader lifecycle, the reopen states, the reconnect reporting, the operator-stop marker and the measured-performance path. It does **not** validate inference, detection, tracking, events or alarms: `inference_state` stayed `NOT_RUNNING` and `detections_observed` stayed 0 because no model is loaded.

## Browser (Microsoft Edge)

```powershell
node scripts/smoke_browser.mjs
```

11 views rendered at 390, 768, 1024, 1440 and 1920 pixels. Zero horizontal overflow after the CSS fix that makes status pills and banners wrap. Zero browser console errors. Zero failed network requests. The webcam start/stop/reopen/stop cycle reported `VALIDATED` through the real UI, driving the same API routes an operator uses.

This validates that the served dashboard renders, that the alarm centre, workers, rules and notifications views are backed by the real API responses, and that the layout does not overflow at any tested width. It does not validate any safety capability.

## HTTP Smoke

```powershell
./scripts/test_frontend_smoke.ps1
```

Result: PASS. Dashboard HTML, navigation, JS/CSS delivery, the health endpoint and the worker-track API all responded. The workers endpoint returned 0 records because no model has ever produced a track. That empty result is the correct answer, and the script does not treat it as a pass on detection.

## Model Validation

```powershell
python scripts/validate_model.py
```

Observed output:

```text
overall_state: MODEL_NOT_CONFIGURED
validation_state: NOT_VALIDATED
probe: SYNTHETIC_PROBE_NOT_REAL_FOOTAGE
  FILE_EXISTS: NOT_RUN (MODEL_NOT_CONFIGURED)
  FILE_READABLE: NOT_RUN (MODEL_NOT_CONFIGURED)
  MODEL_LOADS: NOT_RUN (MODEL_NOT_CONFIGURED)
  CLASSES_PRESENT: NOT_RUN (MODEL_NOT_CONFIGURED)
  INFERENCE_EXECUTES: NOT_RUN (MODEL_NOT_CONFIGURED)
  OUTPUT_SCHEMA_VALID: NOT_RUN (MODEL_NOT_CONFIGURED)
  LATENCY_MEASURED: NOT_RUN (MODEL_NOT_CONFIGURED)
detection_quality_validated: False
```

Exit code 2, meaning no check could run at all. The distinction matters: exit 2 is "blocked", exit 1 is "a check ran and failed". There are no weights in the repository and none were downloaded.

Local model discovery found 0 candidate checkpoints. `.onnx` and `.engine` formats would require `onnxruntime` and `tensorrt`; neither is installed here, so such a checkpoint would report `MODEL_RUNTIME_DEPENDENCY_MISSING` with the package named.

## Notification Transports

```powershell
$payload = @{ recipient = "operator@example.com" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/notifications/test/email -ContentType "application/json" -Body $payload
```

Observed: `status=EMAIL_NOT_CONFIGURED`, `delivery_status=EMAIL_NOT_CONFIGURED`, reason "SMTP host and sender address are required".

```powershell
$payload = @{ recipient = "+15551234567" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/notifications/test/whatsapp -ContentType "application/json" -Body $payload
```

Observed: `status=WHATSAPP_NOT_CONFIGURED`, `delivery_status=WHATSAPP_NOT_CONFIGURED`, reason "Cloud API token, phone number ID and API version are required".

No SMTP credentials and no Meta Cloud API credentials exist on this machine. No email and no WhatsApp message has ever been delivered from this deployment, and `EMAIL_DELIVERED` has never been observed here.

## Physical Alarm

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/alarms/physical/test
```

Observed: `state=PHYSICAL_ALARM_NOT_CONFIGURED`, `hardware_verified=false`. No relay, siren, GPIO line or industrial controller is connected to this laptop. No physical alarm was activated and none is claimed. `paho-mqtt` and `pyserial` are not installed here, so those transports would report `ACTUATOR_NOT_CONNECTED` with the missing dependency named if they were configured.

## Alarm Board

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/alarms/policy
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/alarms
```

Observed: policy enabled, `min_severity` `MEDIUM`, 120 s cooldown, 3 repeats per window, and 0 alarms across every counter. `GET /alarms` returned 0 rows. This is correct: no confirmed safety event has ever been produced on this machine, because there is no model. Alarm behaviour is evidenced by 27 automated tests, not by a live alarm.

## Detector Capability

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/system/detectors
```

Observed:

- `RESTRICTED_ZONE`, `FIRE`, `SMOKE`, `PERSON`, `PHONE`: `implementation_state=IMPLEMENTED`, `availability_state=MODEL_NOT_CONFIGURED`.
- `PPE`, `HELMET`, `SAFETY_VEST`, `PROXIMITY`, `FALL`, `LEAKAGE`, `UNSAFE_BEHAVIOR`: `implementation_state=NOT_IMPLEMENTED`, `availability_state=NOT_AVAILABLE`.
- `operational_detector_count=0`.

Nothing is operational. The `NOT_IMPLEMENTED` group is not merely unconfigured: no code path in this build can produce those verdicts.

## Health

`GET /` and `GET /api/v1/system/health` report `overall_status=DEGRADED` with `degraded_reasons` containing `MODEL_NOT_CONFIGURED`, and `CAMERA_CONFIGURED_NOT_OBSERVED` when the camera is stopped. That is the truthful state of this deployment and is not treated as something to suppress.

## What Is Not Validated

- Detection accuracy, precision, recall, F1, false-positive/false-negative rates. Requires an authorised compatible checkpoint and labelled frames.
- Tracking performance: identity switches, fragmentation, lifecycle errors.
- Real-time performance. Camera capture rate was measured at 8.03 FPS on a laptop USB webcam. Inference FPS was never measured because no inference ran. Nothing here demonstrates real-time operation.
- Live alarm behaviour end to end. No alarm has ever been raised on this machine.
- Notification delivery. Both channels are unconfigured.
- Physical actuation. No hardware is connected.
- IGL plant validation. `NOT_CONFIGURED` / `NOT_VALIDATED`. No authorised IGL footage, plant layout, SOP, threshold source or labelled dataset exists in this workspace, and no substitute data has been created.
- Production deployment: TLS termination, reverse proxy, PostgreSQL topology, secrets manager, multi-worker scheduling. Documented in [docs/deployment.md](docs/deployment.md), not exercised.

## Real-Input Validation Runner

`scripts/run_inference.py` is the separate runner that requires an authorised video source as well as a model. It has not been run to completion on this machine, because neither input exists.

### Authorized Inputs

For a machine-level preflight on Windows, run `./scripts/validate_system.ps1`. It checks installed runtime packages, searches the repository for model files, attempts a real OpenCV webcam capture, and runs tests using a temporary directory in the repository. It never sends provider test messages or claims browser permission, model inference, or plant validation.

The dashboard also has a separate browser webcam preview. It requests permission only after the user presses **Allow & Start Preview**, enumerates video devices after permission, displays measured browser video frames/FPS, and releases the media tracks when stopped or leaving Cameras. These frames remain in the browser and are not passed to backend inference. Browser camera access requires localhost or HTTPS.

Configure exactly one input:

- `VIDEO_SOURCE`: path to an authorized local `.mp4`, `.avi`, `.mov`, or `.mkv` file.
- `RTSP_URL`: authorized RTSP/RTSPS camera URL. This setting is secret-typed. Do not put it in source, a command history, test output, reports, screenshots, or Git. Prefer this environment setting over `--source` for credential-bearing URLs.

Configure the actual local model file and its identity:

- `MODEL_WEIGHTS_PATH`: existing, readable checkpoint file. No download or fallback occurs.
- `MODEL_NAME`: explicit model name.
- `MODEL_VERSION`: explicit version/checkpoint identifier.
- `MODEL_CONFIDENCE_THRESHOLD`: model confidence threshold from `0` through `1`.

Do not set both source variables. An explicit `--source` overrides environment source values. Missing source prints `VIDEO_SOURCE_NOT_CONFIGURED`; absent model weights print `MODEL_NOT_CONFIGURED`. Invalid file, model load, or identity configuration fails with a distinct error status and does not proceed with fake results.

### Command

```powershell
python scripts/run_inference.py --source "C:\authorized\path\sample.mp4"
python scripts/run_inference.py --duration-seconds 30
```

The first command processes a local file to EOF unless a duration limit is provided. The second uses configured `RTSP_URL` or `VIDEO_SOURCE`; streams default to 30 seconds. Use `--report <path>` to select another report location. Protect reports as operational data. Reports contain only a source type and safe identifier: local file basename or credential-redacted stream URL with query values removed. The full model path is reduced to its filename.

The command executes the existing `StreamReader` and rolling `FrameBuffer`, then the explicitly loaded adapter, detection structure, and IoU tracker. RTSP reconnects are counted after a successful reconnection. A failed source, frame decode, model load, inference, or tracker operation is reported; the process never substitutes another model.

### Report Fields And Status Meaning

The JSON report records the input provenance, OS/Python/OpenCV/PyTorch/Ultralytics versions; model name/version/weights filename/confidence/load state; input type/resolution/source FPS/duration and stream terminal state; received and processed frames; inference attempts/completions and average measured latency; processing FPS; detections; unique visual tracks and lifecycle transitions; dropped frames, inference/tracking errors, reconnects, and pipeline errors.

`input_provenance` is `AUTHORIZED_REAL_INPUT` for a normal command-line run. The test suite passes `TEST_FIXTURE` explicitly, and the CLI has no flag to select it, so a test run can never be recorded as real-input validation.

- `NOT_VALIDATED`: input/model/configuration failed or the pipeline did not complete meaningful work. A run that could not start still writes a report recording the blocking reason and zero pipeline work.
- `TEST_FIXTURE_EXECUTION`: the pipeline completed cleanly using test doubles. This is a software test result only and is never a validation claim.
- `TECHNICAL_PIPELINE_VALIDATED`: reserved for a real-source ingestion-only validation where the model is unavailable; this runner currently requires a configured model before starting the full pipeline.
- `REAL_INPUT_VALIDATED`: authorized real source frames and successful model inference traversed the pipeline without recorded inference/tracking/source errors. This is technical execution only, not accuracy validation. Report assembly refuses this status if the input provenance is not authorized, if no frame was processed, if no inference completed, or if any error is recorded.
- `IGL_VALIDATED`: never assigned by this command.

`source_fps` is read from the input capture metadata. `processing_fps` is the measured count of frames handed through the pipeline divided by the processing window, which is measured before teardown so shutdown time cannot depress the figure. Neither is inferred from the other. A report does not establish accuracy or prove real-time operation. The report always records `igl_validated: false`.

## Evidence Required For Future Claims

- **Detection validation:** authorised representative footage, frame-level ground-truth boxes/classes, declared evaluation protocol, and measured precision/recall/F1 with error analysis.
- **Tracking validation:** labelled identities across frames, defined association metrics, and measured identity switches, fragmentation and lifecycle errors.
- **Real-time performance:** sustained representative source and deployment hardware, source-vs-processing rate comparison, latency distribution, backlog/drop measurements, and documented operating duration.
- **Alarm validation:** a real confirmed event, a live alarm raised from it, and the acknowledge/escalate/clear sequence performed against real hardware the operator controls.
- **IGL validation:** explicit authorisation, representative IGL camera footage, documented plant/camera conditions, approved SOP/rules where applicable, labelled IGL validation data, and a reviewed independent evaluation.

No authorised IGL camera footage, plant layout, PPE SOP dataset, model checkpoint, or labelled IGL validation dataset is present in this workspace. No synthetic operational data has been created to stand in for any of them.
