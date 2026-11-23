#!/usr/bin/env python
"""Independent leakage model evaluation with per-class metrics and error analysis.

This script uses only genuine dataset images and annotations. It does not create
synthetic detections or metrics.
"""

import argparse
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from leakage_training import LEAKAGE_CLASSES, _find_label, _load_dataset_yaml, _parse_label


def _iou_xyxy(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _to_xyxy(center_x, center_y, width, height, image_width, image_height):
    return (
        (center_x - width / 2) * image_width,
        (center_y - height / 2) * image_height,
        (center_x + width / 2) * image_width,
        (center_y + height / 2) * image_height,
    )


def _extract_per_class(results, model):
    box = getattr(results, "box", None)
    if box is None:
        return []
    indices = getattr(box, "ap_class_index", None)
    if indices is None:
        return []
    indices = list(indices)
    rows = []
    for index, class_index in enumerate(indices):
        class_id = int(class_index)
        name = model.names.get(class_id, str(class_id)) if isinstance(model.names, dict) else model.names[class_id]
        rows.append(
            {
                "class_id": class_id,
                "class_name": name,
                "precision": float(box.p[index]),
                "recall": float(box.r[index]),
                "mAP50": float(box.ap50[index]),
                "mAP50_95": float(box.ap[index]),
            }
        )
    return rows


def _confusion_matrix(results):
    cm = getattr(results, "confusion_matrix", None)
    if cm is None:
        return None
    matrix = getattr(cm, "matrix", None)
    names = getattr(cm, "names", None)
    if matrix is None:
        return None
    return {"labels": names, "matrix": np.asarray(matrix).tolist()}


def _error_analysis(model, images, labels, conf, imgsz):
    stats = {
        class_name: {"tp": 0, "fp": 0, "fn": 0}
        for class_name in LEAKAGE_CLASSES
    }
    false_positives = []
    false_negatives = []
    total_gt = 0
    total_pred = 0
    total_tp = 0
    for image_path in images:
        label_path = labels.get(image_path)
        if not label_path:
            continue
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        height, width = image.shape[:2]
        gt = []
        for class_id, center_x, center_y, bw, bh in _parse_label(label_path, LEAKAGE_CLASSES, canonical=False):
            gt.append(
                {
                    "class_id": class_id,
                    "class_name": LEAKAGE_CLASSES[class_id],
                    "xyxy": _to_xyxy(center_x, center_y, bw, bh, width, height),
                }
            )
        total_gt += len(gt)
        result = model.predict(source=image, conf=conf, imgsz=imgsz, iou=0.5, save=False, verbose=False)[0]
        unmatched = set(range(len(gt)))
        if result.boxes is not None:
            for box in result.boxes:
                class_id = int(box.cls[0].item())
                class_name = model.names.get(class_id, str(class_id)) if isinstance(model.names, dict) else model.names[class_id]
                confidence = float(box.conf[0].item())
                xyxy = tuple(float(value) for value in box.xyxy[0].tolist())
                total_pred += 1
                best_index = None
                best_iou = 0.0
                for gt_index in unmatched:
                    if gt[gt_index]["class_id"] != class_id:
                        continue
                    iou = _iou_xyxy(xyxy, gt[gt_index]["xyxy"])
                    if iou > best_iou:
                        best_iou = iou
                        best_index = gt_index
                if best_index is not None and best_iou >= 0.5:
                    unmatched.remove(best_index)
                    stats[class_name]["tp"] += 1
                    total_tp += 1
                else:
                    stats[class_name]["fp"] += 1
                    if len(false_positives) < 50:
                        false_positives.append(
                            {
                                "image": str(image_path),
                                "predicted_class": class_name,
                                "confidence": round(confidence, 4),
                                "best_iou": round(best_iou, 4),
                                "xyxy": [round(value, 1) for value in xyxy],
                            }
                        )
        for gt_index in unmatched:
            stats[gt[gt_index]["class_name"]]["fn"] += 1
            if len(false_negatives) < 50:
                false_negatives.append(
                    {
                        "image": str(image_path),
                        "gt_class": gt[gt_index]["class_name"],
                        "xyxy": [round(value, 1) for value in gt[gt_index]["xyxy"]],
                    }
                )
    for class_name, values in stats.items():
        tp, fp, fn = values["tp"], values["fp"], values["fn"]
        values["precision"] = round(tp / (tp + fp), 4) if tp + fp else 0.0
        values["recall"] = round(tp / (tp + fn), 4) if tp + fn else 0.0
        values["f1"] = round(2 * values["precision"] * values["recall"] / (values["precision"] + values["recall"]), 4) if values["precision"] + values["recall"] else 0.0
    return {
        "confidence_threshold": conf,
        "iou_threshold": 0.5,
        "total_ground_truth": total_gt,
        "total_predictions": total_pred,
        "total_true_positives": total_tp,
        "per_class": stats,
        "false_positive_examples": false_positives,
        "false_negative_examples": false_negatives,
    }


def _sample_predictions(model, images, output_dir, imgsz, limit=12):
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for index, image_path in enumerate(images[:limit]):
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        result = model.predict(source=image, conf=0.25, imgsz=imgsz, save=False, verbose=False)[0]
        annotated = result.plot()
        output = output_dir / f"sample_{index + 1:02d}.jpg"
        cv2.imwrite(str(output), annotated)
        saved.append(str(output))
    return saved


def evaluate(weights_path, dataset_root, split, imgsz, conf, output_dir):
    root = Path(dataset_root).resolve()
    yaml_path, yaml_text, names = _load_dataset_yaml(root)
    model = YOLO(weights_path)
    results = model.val(
        data=str(yaml_path),
        split=split,
        imgsz=imgsz,
        conf=conf,
        iou=0.5,
        plots=True,
        project=str(output_dir / "runs"),
        name="validation",
        exist_ok=True,
    )
    image_root = root / split / "images"
    label_root = root / split / "labels"
    images = sorted(image_root.glob("*.jpg")) + sorted(image_root.glob("*.png"))
    labels = {image: _find_label(image) for image in images}
    images_with_labels = [image for image in images if labels.get(image)]
    error_analysis = _error_analysis(model, images_with_labels, labels, conf, imgsz)
    samples = _sample_predictions(model, images_with_labels, output_dir / "samples", imgsz)
    started = time.time()
    for image in images_with_labels[:20]:
        model.predict(source=cv2.imread(str(image)), conf=conf, imgsz=imgsz, save=False, verbose=False)
    elapsed = time.time() - started
    model_size = Path(weights_path).stat().st_size if Path(weights_path).exists() else None
    report = {
        "weights": str(Path(weights_path)),
        "model": {
            "weights": str(Path(weights_path)),
            "version": Path(weights_path).parent.name,
        },
        "dataset_root": str(root),
        "split": split,
        "imgsz": imgsz,
        "conf": conf,
        "results_dict": results.results_dict,
        "per_class": _extract_per_class(results, model),
        "confusion_matrix": _confusion_matrix(results),
        "error_analysis": error_analysis,
        "sample_predictions": samples,
        "inference_speed_ms": results.speed,
        "manual_inference_ms_per_image": round((elapsed / max(1, len(images_with_labels[:20]))) * 1000, 2),
        "model_size_bytes": model_size,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = [
        f"# Leakage Model Evaluation: `{Path(weights_path).parent.name}`",
        "",
        f"- Dataset: `{root}`",
        f"- Split: `{split}`",
        f"- Images: {len(images_with_labels)}",
        f"- Imgsz: {imgsz}",
        f"- Confidence threshold: {conf}",
        "",
        "## Aggregate metrics",
        "",
        "| Precision | Recall | mAP50 | mAP50-95 |",
        "|---|---|---|---|",
    ]
    results_dict = results.results_dict
    lines.append(
        f"| {results_dict.get('metrics/precision(B)', 0):.4f} | "
        f"{results_dict.get('metrics/recall(B)', 0):.4f} | "
        f"{results_dict.get('metrics/mAP50(B)', 0):.4f} | "
        f"{results_dict.get('metrics/mAP50-95(B)', 0):.4f} |"
    )
    lines += ["", "## Per-class metrics", "", "| Class | Precision | Recall | mAP50 | mAP50-95 | TP | FP | FN |", "|---|---|---|---|---|---|---|---|"]
    for row in report["per_class"]:
        error = error_analysis["per_class"].get(row["class_name"], {})
        lines.append(
            f"| {row['class_name']} | {row['precision']:.4f} | {row['recall']:.4f} | "
            f"{row['mAP50']:.4f} | {row['mAP50_95']:.4f} | {error.get('tp', 0)} | {error.get('fp', 0)} | {error.get('fn', 0)} |"
        )
    lines += ["", "## Error analysis", "", f"- Ground truth boxes: {error_analysis['total_ground_truth']}", f"- Predictions: {error_analysis['total_predictions']}", f"- True positives: {error_analysis['total_true_positives']}", "", "Sample prediction images are in `samples/`.", "", "Confusion matrix and curves are in `runs/validation/`."]
    (output_dir / "evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", required=True)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--imgsz", type=int, default=192)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = evaluate(args.weights, args.dataset_root, args.split, args.imgsz, args.conf, Path(args.output_dir))
    print(json.dumps({"output_dir": str(Path(args.output_dir)), "results_dict": report["results_dict"], "per_class": report["per_class"]}, indent=2, default=str))


if __name__ == "__main__":
    main()