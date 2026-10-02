# Phase 4 Real-Input Validation

## Authorized Inputs

For a machine-level preflight on Windows, run `./scripts/validate_system.ps1`. It checks installed runtime packages, searches the repository for model files, attempts a real OpenCV webcam capture, and runs tests using a temporary directory in the repository. It never sends provider test messages or claims browser permission, model inference, or plant validation. The report from the webcam probe is the evidence for device availability on that machine.

The dashboard also has a separate browser webcam preview. It requests permission only after the user presses **Allow & Start Preview**, enumerates video devices after permission, displays measured browser video frames/FPS, and releases the media tracks when stopped or leaving Cameras. These frames remain in the browser and are not passed to backend inference. Browser camera access requires localhost or HTTPS.

Install the pinned requirements and copy `.env.example` to `.env`. Keep `.env` local; it is git-ignored. Configure exactly one input:

- `VIDEO_SOURCE`: path to an authorized local `.mp4`, `.avi`, `.mov`, or `.mkv` file.
- `RTSP_URL`: authorized RTSP/RTSPS camera URL. This setting is secret-typed. Do not put it in source, a command history, test output, reports, screenshots, or Git. Prefer this environment setting over `--source` for credential-bearing URLs.

Configure the actual local model file and its identity:

- `MODEL_WEIGHTS_PATH`: existing, readable `.pt` file. No download or fallback occurs.
- `MODEL_NAME`: explicit model name.
- `MODEL_VERSION`: explicit version/checkpoint identifier.
- `MODEL_CONFIDENCE_THRESHOLD`: model confidence threshold from `0` through `1`.

Do not set both source variables. An explicit `--source` overrides environment source values. Missing source prints `VIDEO_SOURCE_NOT_CONFIGURED`; absent model weights print `MODEL_NOT_CONFIGURED`. Invalid file, model load, or identity configuration fails with a distinct error status and does not proceed with fake results.

## Command

From the project root:

```powershell
python scripts/run_inference.py --source "C:\authorized\path\sample.mp4"
python scripts/run_inference.py --duration-seconds 30
```

The first command processes a local file to EOF unless a duration limit is provided. The second uses configured `RTSP_URL` or `VIDEO_SOURCE`; streams default to 30 seconds. Use `--report <path>` to select another report location. Protect reports as operational data. Reports contain only a source type and safe identifier: local file basename or credential-redacted stream URL with query values removed. The full model path is reduced to its filename.

The command executes the existing `StreamReader` and rolling `FrameBuffer`, then the explicitly loaded adapter, detection structure, and IoU tracker. RTSP reconnects are counted after a successful reconnection. A failed source, frame decode, model load, inference, or tracker operation is reported; the process never substitutes another model.

## Report Fields

The JSON report records the input provenance, OS/Python/OpenCV/PyTorch/Ultralytics versions; model name/version/weights filename/confidence/load state; input type/resolution/source FPS/duration and stream terminal state; received and processed frames; inference attempts/completions and average measured latency; processing FPS; detections; unique visual tracks and lifecycle transitions; dropped frames, inference/tracking errors, reconnects, and pipeline errors.

`input_provenance` is `AUTHORIZED_REAL_INPUT` for a normal command-line run. The test suite passes `TEST_FIXTURE` explicitly, and the CLI has no flag to select it, so a test run can never be recorded as real-input validation.

`source_fps` is read from the input capture metadata. `processing_fps` is the measured count of frames handed through the pipeline divided by the processing window, which is measured before teardown so shutdown time cannot depress the figure. Neither is inferred from the other. A report does not establish accuracy or prove real-time operation.

The report is complete when `create_report` returns. The redacted source identifier and the stream terminal state are part of report construction, not patched in afterwards.

## Status Meaning

- `NOT_VALIDATED`: input/model/configuration failed or the pipeline did not complete meaningful work. A run that could not start still writes a report recording the blocking reason and zero pipeline work.
- `TEST_FIXTURE_EXECUTION`: the pipeline completed cleanly using test doubles. This is a software test result only and is never a validation claim.
- `TECHNICAL_PIPELINE_VALIDATED`: reserved for a real-source ingestion-only validation where the model is unavailable; this runner currently requires a configured model before starting the full pipeline.
- `REAL_INPUT_VALIDATED`: authorized real source frames and successful model inference traversed the pipeline without recorded inference/tracking/source errors. This is technical execution only, not accuracy validation. Report assembly refuses this status if the input provenance is not authorized, if no frame was processed, if no inference completed, or if any error is recorded.
- `IGL_VALIDATED`: never assigned by this command.

The report always records `igl_validated: false`.

## Evidence Required For Claims

- **Detection validation:** authorized representative footage, frame-level ground-truth boxes/classes, declared evaluation protocol, and measured precision/recall/F1 or appropriate detection metrics with error analysis.
- **Tracking validation:** labeled identities across frames, defined association metrics, and measured identity switches, fragmentation, and track lifecycle errors.
- **Real-time performance:** sustained representative source and deployment hardware, source-vs-processing rate comparison, latency distribution, backlog/drop measurements, and documented operating duration. Do not call it real-time unless processing keeps up under the intended conditions.
- **IGL validation:** explicit authorization, representative IGL camera footage, documented plant/camera conditions, approved SOP/rules where applicable, labeled IGL validation data, and a reviewed independent evaluation. No such material is currently present in this workspace.

No authorized IGL camera footage, plant layout, PPE SOP dataset, model checkpoint, or labeled IGL validation dataset is currently present. In the current Windows validation, PnP listed a UVC webcam in `OK` state. A sandboxed standalone OpenCV probe failed to open it, while the isolated API process started outside that command sandbox discovered and opened device 0 through DirectShow, observed real frames at 640×480, measured approximately 8 FPS, returned a JPEG snapshot, and stopped/reopened successfully. This process-specific discrepancy must be checked by running the supplied probe in the operator's normal Windows terminal. The runner requires model weights and is not a webcam test. Model-backed validation remains `NOT_VALIDATED` until an authorized compatible checkpoint and source are supplied.
