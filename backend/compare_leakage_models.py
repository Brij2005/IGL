#!/usr/bin/env python
"""Compare leakage model evaluation JSON files produced by evaluate_leakage.py."""

import argparse
import json
from pathlib import Path


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation", action="append", required=True, help="Path to evaluation.json (repeatable)")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    evaluations = [(Path(path).parent.name, _load(path)) for path in args.evaluation]
    lines = [
        "# Leakage Model Comparison",
        "",
        "All metrics come from the same independent test split and the same evaluation script.",
        "",
        "## Aggregate metrics",
        "",
        "| Model | Version | Precision | Recall | mAP50 | mAP50-95 | Inference ms | Model size |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, evaluation in evaluations:
        metrics = evaluation.get("results_dict", {})
        lines.append(
            f"| {label} | {evaluation.get('model', {}).get('version', '-')} | "
            f"{metrics.get('metrics/precision(B)', 0):.4f} | {metrics.get('metrics/recall(B)', 0):.4f} | "
            f"{metrics.get('metrics/mAP50(B)', 0):.4f} | {metrics.get('metrics/mAP50-95(B)', 0):.4f} | "
            f"{evaluation.get('manual_inference_ms_per_image', 0):.2f} | {evaluation.get('model_size_bytes', 0)} |"
        )
    lines += ["", "## Per-class mAP50", "", "| Class | " + " | ".join(label for label, _ in evaluations) + " |", "|---|" + "---|" * len(evaluations)]
    classes = []
    for _, evaluation in evaluations:
        for row in evaluation.get("per_class", []):
            if row["class_name"] not in classes:
                classes.append(row["class_name"])
    for class_name in classes:
        values = []
        for _, evaluation in evaluations:
            row = next((item for item in evaluation.get("per_class", []) if item["class_name"] == class_name), None)
            values.append(f"{row['mAP50']:.4f}" if row else "-")
        lines.append(f"| {class_name} | " + " | ".join(values) + " |")
    lines += ["", "## Notes", ""]
    for label, evaluation in evaluations:
        lines.append(f"- `{label}`: {len(evaluation.get('sample_predictions', []))} sample prediction images; {evaluation.get('error_analysis', {}).get('total_ground_truth', 0)} ground-truth boxes.")
    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()