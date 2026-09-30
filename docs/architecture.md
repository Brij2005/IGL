# Architecture

## Phase 4 Data Path

`Camera configuration -> StreamReader -> bounded FrameBuffer -> UltralyticsModelAdapter -> Detection -> IoUTracker`

`StreamReader` accepts a configured RTSP/HTTP source or local video path. Frames are timestamped and copied into a thread-safe buffer bounded by retention time, frame count, and bytes. The adapter loads only an explicitly configured local weights file. Missing or invalid weights produce a visible model status and zero detections. Inference access is serialized across camera workers.

Each `Detection` validates camera, timestamp, class, confidence, bounding box, and model metadata. Each `VisualTrack` has a generated visual-session identifier and a `DETECTED`, `TRACKED`, `TEMPORARILY_LOST`, or `ENDED` lifecycle. Track identifiers are not employee identities; identity is not inferred or attached.

The authenticated `/api/v1/system/ai-health` endpoint exposes model and per-camera pipeline states. `/api/v1/system/pipelines` exposes pipeline status only. No event, PPE, proximity, fall, fire, evidence, or alert generation is connected in Phase 4.

## Configuration and Validation Boundaries

- **IMPLEMENTED:** Bounded frame buffering, local model adapter, detection structure, IoU track association, and camera-to-pipeline wiring.
- **PARTIALLY IMPLEMENTED:** File and RTSP acquisition code. No actual video source was available for end-to-end verification.
- **NOT_CONFIGURED:** Model weights are absent; model health reports `MODEL_NOT_CONFIGURED`.
- **NOT_VALIDATED:** No IGL layouts, cameras, SOPs, or labeled IGL data were supplied or assessed.

The system is advisory only. No safety instrumented or operational control systems are called.