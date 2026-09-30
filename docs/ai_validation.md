# AI Validation Status

## Current Result

- Model weights: **UNCONFIGURED**. No weights are present in the workspace.
- Real video: **NOT AVAILABLE**. No MP4, AVI, MKV, MOV, or authorized RTSP test source is present.
- Detection and tracking performance: **NOT_VALIDATED**. Real-time detection, FPS, accuracy, precision, recall, F1, and tracking accuracy have not been measured.
- IGL validation: **NOT_VALIDATED**. No authorized IGL footage or labeled dataset has been evaluated.
- Real-input validation runner: **BLOCKED_BY_REAL_INPUT**. `scripts/run_inference.py` is implemented and tested against controlled fixtures, but it has never been run against authorized real input in this workspace.

No authorized IGL camera footage, plant layout, PPE SOP dataset, model checkpoint, or labeled IGL validation dataset is present. No substitute or synthetic data has been created to stand in for them.

The model adapter reports configuration/load/inference errors and does not create placeholder results. Tests exercise unavailable/invalid model states and component behavior; a clean test run is recorded as `TEST_FIXTURE_EXECUTION`, which is a software test result and not a model-accuracy measurement or IGL validation.

## Next Validation Gate

Before reporting model performance, authorize and provide representative video and frame-level labels, document camera conditions and the model/version/configuration, run the actual pipeline, and calculate precision, recall, F1, false-positive/negative rates, latency, and FPS from measured outputs. Keep the resulting report explicitly scoped to that dataset and setup.