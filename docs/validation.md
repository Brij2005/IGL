# Phase 4 Real-Input Validation

## Authorized Inputs

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

The JSON report records OS/Python/OpenCV/PyTorch/Ultralytics versions; model name/version/weights filename/confidence/load state; input type/resolution/source FPS/duration; received and processed frames; inference attempts/completions and average measured latency; processing FPS; detections; unique visual tracks and lifecycle transitions; dropped frames, inference/tracking errors, reconnects, and pipeline errors.

`source_fps` is read from the input capture metadata. `processing_fps` is the measured count of frames handed through the pipeline divided by elapsed wall-clock processing duration. Neither is inferred from the other. A report does not establish accuracy or prove real-time operation.

## Status Meaning

- `NOT_VALIDATED`: input/model/configuration failed or the pipeline did not complete meaningful work.
- `TECHNICAL_PIPELINE_VALIDATED`: reserved for a real-source ingestion-only validation where the model is unavailable; this runner currently requires a configured model before starting the full pipeline.
- `REAL_INPUT_VALIDATED`: real source frames and successful model inference traversed the pipeline without recorded inference/tracking/source errors. This is technical execution only, not accuracy validation.
- `IGL_VALIDATED`: never assigned by this command.

The report always records `igl_validated: false`.

## Evidence Required For Claims

- **Detection validation:** authorized representative footage, frame-level ground-truth boxes/classes, declared evaluation protocol, and measured precision/recall/F1 or appropriate detection metrics with error analysis.
- **Tracking validation:** labeled identities across frames, defined association metrics, and measured identity switches, fragmentation, and track lifecycle errors.
- **Real-time performance:** sustained representative source and deployment hardware, source-vs-processing rate comparison, latency distribution, backlog/drop measurements, and documented operating duration. Do not call it real-time unless processing keeps up under the intended conditions.
- **IGL validation:** explicit authorization, representative IGL camera footage, documented plant/camera conditions, approved SOP/rules where applicable, labeled IGL validation data, and a reviewed independent evaluation. No such material is currently present in this workspace.

No authorized IGL camera footage, plant layout, PPE SOP dataset, or labeled IGL validation dataset is currently present in this workspace. The current workspace has no model weights or real video source, so validation remains `NOT_VALIDATED` until both are supplied.