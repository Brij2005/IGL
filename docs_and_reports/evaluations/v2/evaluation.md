# Leakage Model Evaluation: `v2`

- Dataset: `C:\Users\Krishna Sharma\Desktop\IGL\backend\data\leakage_multiclass_balanced`
- Split: `test`
- Images: 2377
- Imgsz: 192
- Confidence threshold: 0.25

## Aggregate metrics

| Precision | Recall | mAP50 | mAP50-95 |
|---|---|---|---|
| 0.2791 | 0.2197 | 0.1377 | 0.0874 |

## Per-class metrics

| Class | Precision | Recall | mAP50 | mAP50-95 | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| Water Dripping | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 | 66 |
| Water Spraying | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 | 121 |
| Visible Water Flow | 0.3230 | 0.3077 | 0.1099 | 0.0681 | 76 | 164 | 262 |
| Pipe Joint Leakage | 0.4641 | 0.2942 | 0.1825 | 0.1198 | 197 | 214 | 527 |
| Water Accumulation | 0.4464 | 0.4822 | 0.3770 | 0.2468 | 523 | 455 | 626 |
| Normal/Dry Pipeline | 0.4414 | 0.2344 | 0.1570 | 0.0895 | 139 | 201 | 552 |

## Error analysis

- Ground truth boxes: 3089
- Predictions: 1969
- True positives: 935

Sample prediction images are in `samples/`.

Confusion matrix and curves are in `runs/validation/`.
