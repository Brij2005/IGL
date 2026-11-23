# Leakage Model Evaluation: `v1`

- Dataset: `C:\Users\Krishna Sharma\Desktop\IGL\backend\data\leakage_multiclass_balanced`
- Split: `test`
- Images: 2377
- Imgsz: 192
- Confidence threshold: 0.25

## Aggregate metrics

| Precision | Recall | mAP50 | mAP50-95 |
|---|---|---|---|
| 0.0628 | 0.0529 | 0.0419 | 0.0267 |

## Per-class metrics

| Class | Precision | Recall | mAP50 | mAP50-95 | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| Water Dripping | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 | 66 |
| Water Spraying | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 | 121 |
| Visible Water Flow | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 | 338 |
| Pipe Joint Leakage | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 | 724 |
| Water Accumulation | 0.3767 | 0.3177 | 0.2514 | 0.1600 | 625 | 4250 | 524 |
| Normal/Dry Pipeline | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 | 691 |

## Error analysis

- Ground truth boxes: 3089
- Predictions: 4875
- True positives: 625

Sample prediction images are in `samples/`.

Confusion matrix and curves are in `runs/validation/`.
