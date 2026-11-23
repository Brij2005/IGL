# Leakage Model Evaluation: `v3`

- Dataset: `C:\Users\Krishna Sharma\Desktop\IGL\backend\data\leakage_multiclass_balanced`
- Split: `test`
- Images: 2377
- Imgsz: 192
- Confidence threshold: 0.25

## Aggregate metrics

| Precision | Recall | mAP50 | mAP50-95 |
|---|---|---|---|
| 0.4760 | 0.2689 | 0.1853 | 0.1271 |

## Per-class metrics

| Class | Precision | Recall | mAP50 | mAP50-95 | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| Water Dripping | 0.6000 | 0.0909 | 0.0669 | 0.0242 | 3 | 2 | 63 |
| Water Spraying | 0.1453 | 0.1405 | 0.0226 | 0.0083 | 17 | 107 | 104 |
| Visible Water Flow | 0.3537 | 0.2396 | 0.0850 | 0.0607 | 36 | 117 | 302 |
| Pipe Joint Leakage | 0.5751 | 0.3757 | 0.2941 | 0.2013 | 267 | 207 | 457 |
| Water Accumulation | 0.6767 | 0.5683 | 0.5282 | 0.3934 | 625 | 237 | 524 |
| Normal/Dry Pipeline | 0.5055 | 0.1983 | 0.1151 | 0.0744 | 158 | 180 | 533 |

## Error analysis

- Ground truth boxes: 3089
- Predictions: 1956
- True positives: 1106

Sample prediction images are in `samples/`.

Confusion matrix and curves are in `runs/validation/`.
