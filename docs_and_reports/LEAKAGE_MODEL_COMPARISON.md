# Leakage Model Comparison

All metrics come from the same independent test split and the same evaluation script.

## Aggregate metrics

| Model | Version | Precision | Recall | mAP50 | mAP50-95 | Inference ms | Model size |
|---|---|---|---|---|---|---|---|
| v1 | v1 | 0.0628 | 0.0529 | 0.0419 | 0.0267 | 47.25 | 6450666 |
| v2 | v2 | 0.2791 | 0.2197 | 0.1377 | 0.0874 | 41.27 | 6452074 |
| v3 | v3 | 0.4760 | 0.2689 | 0.1853 | 0.1271 | 54.74 | 6453994 |

## Per-class mAP50

| Class | v1 | v2 | v3 |
|---|---|---|---|
| Water Dripping | 0.0000 | 0.0000 | 0.0669 |
| Water Spraying | 0.0000 | 0.0000 | 0.0226 |
| Visible Water Flow | 0.0000 | 0.1099 | 0.0850 |
| Pipe Joint Leakage | 0.0000 | 0.1825 | 0.2941 |
| Water Accumulation | 0.2514 | 0.3770 | 0.5282 |
| Normal/Dry Pipeline | 0.0000 | 0.1570 | 0.1151 |

## Notes

- `v1`: 12 sample prediction images; 3089 ground-truth boxes.
- `v2`: 12 sample prediction images; 3089 ground-truth boxes.
- `v3`: 12 sample prediction images; 3089 ground-truth boxes.
