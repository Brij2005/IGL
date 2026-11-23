"""Pipeline corrosion dataset ingestion, validation, training and evaluation.

This module reuses the generic YOLO dataset primitives already implemented in
``leakage_training`` (yaml loading, label parsing, split assignment, dataset
registration) so there is exactly one dataset/training implementation in the
project. Only corrosion-specific concerns live here:

    * the class taxonomy (external corrosion, surface rust, coating damage,
      surface deterioration, normal pipe surface)
    * the dataset card and licensing/source record
    * corrosion-specific validation rules

Training is only possible when a genuine labelled corrosion dataset is supplied.
No labels are invented and unrelated pipeline defects are never relabelled as
corrosion.

CLI:
    python corrosion_training.py validate   --dataset <dir>
    python corrosion_training.py prepare    --dataset <dir> [--output <dir>]
    python corrosion_training.py register   --dataset <dir>
    python corrosion_training.py train      --dataset <dir> [--epochs N] [--version 1]
    python corrosion_training.py evaluate   --weights <path> --dataset <dir> [--split test]
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from config import BACKEND_DIR, MODEL_DIR
from database import get_db_connection, now_iso
from leakage_training import (
    _assign_splits,
    _dataset_image_roots,
    _find_label,
    _load_dataset_yaml,
    _parse_label,
    register_dataset,
)

MODEL_ID = "model-pipe-corrosion"

# Corrosion taxonomy. Only these classes are supported; anything else in a
# source dataset is reported as unmapped and excluded unless explicitly mapped.
CORROSION_CLASSES = [
    "external_corrosion",
    "surface_rust",
    "coating_damage",
    "surface_deterioration",
    "normal_pipe_surface",
]

# Common aliases seen in public corrosion datasets -> canonical class.
CLASS_ALIASES = {
    "corrosion": "external_corrosion",
    "external corrosion": "external_corrosion",
    "external-corrosion": "external_corrosion",
    "pipe corrosion": "external_corrosion",
    "rust": "surface_rust",
    "surface rust": "surface_rust",
    "surface-rust": "surface_rust",
    "rusting": "surface_rust",
    "coating damage": "coating_damage",
    "coating-damage": "coating_damage",
    "coating failure": "coating_damage",
    "paint damage": "coating_damage",
    "deterioration": "surface_deterioration",
    "surface deterioration": "surface_deterioration",
    "surface-deterioration": "surface_deterioration",
    "degradation": "surface_deterioration",
    "normal": "normal_pipe_surface",
    "normal pipe": "normal_pipe_surface",
    "normal pipe surface": "normal_pipe_surface",
    "no corrosion": "normal_pipe_surface",
    "no-corrosion": "normal_pipe_surface",
    "healthy": "normal_pipe_surface",
}

DATASET_CARD = {
    "name": "IGL Pipeline Corrosion",
    "task": "object_detection",
    "classes": CORROSION_CLASSES,
    "accepted_sources": [
        {
            "source": "Roboflow Universe - Corrosion-Pipelines-T1 (t1-qwfwj/corrosion-pipelines-t1-mx3lq)",
            "url": "https://universe.roboflow.com/t1-qwfwj/corrosion-pipelines-t1-mx3lq",
            "license": "Confirm the license shown on the dataset page before redistribution",
        },
        {
            "source": "Roboflow Universe - Corrosion Detection in Subsea Pipelines (corrosion-detection-zvmog)",
            "url": "https://universe.roboflow.com/corrosion-detection-zvmog/corrosion-detection-in-subsea-pipelines",
            "license": "Confirm the license shown on the dataset page before redistribution",
        },
        {
            "source": "Roboflow Universe - Pipeline-Corrosion (saurav-kumar-2jllo)",
            "url": "https://universe.roboflow.com/saurav-kumar-2jllo/pipeline-corrosion",
            "license": "Confirm the license shown on the dataset page before redistribution",
        },
    ],
    "class_policy": {
        "allowed": CORROSION_CLASSES,
        "aliases": CLASS_ALIASES,
        "unmapped_policy": "excluded_and_reported",
        "note": (
            "Cracks, dents, weld defects and other non-corrosion pipeline defects are never "
            "relabelled as corrosion. Rust appearance alone is never proof of structural failure."
        ),
    },
    "acquisition_status": "BLOCKED",
    "acquisition_blockers": [
        "No genuine labelled corrosion dataset is present locally",
        "Roboflow programmatic download was attempted and did not produce a usable archive in this environment",
        "No GPU is available; CPU-only training budget is insufficient for a defensible model",
    ],
}


def _normalise_class(name: str):
    key = (name or "").strip().lower()
    if key in CORROSION_CLASSES:
        return key
    return CLASS_ALIASES.get(key)


def validate_dataset(dataset_root: str) -> dict:
    """Validate a genuine corrosion YOLO dataset without inventing labels."""
    root = Path(dataset_root)
    if not root.exists():
        raise FileNotFoundError(f"Dataset root not found: {root}")
    yaml_path, yaml_text, source_names = _load_dataset_yaml(root)
    roots = _dataset_image_roots(root, yaml_path, yaml_text)
    images = []
    for image_root in roots:
        for pattern in ("*.jpg", "*.jpeg", "*.png"):
            images.extend(image_root.rglob(pattern))
    images = sorted(set(images))

    malformed = []
    missing_labels = 0
    empty_labels = 0
    unmapped = {}
    class_distribution = {name: 0 for name in CORROSION_CLASSES}

    for image in images:
        label = _find_label(image)
        if not label:
            missing_labels += 1
            continue
        try:
            boxes = _parse_label(label, source_names, canonical=False)
            if not boxes:
                empty_labels += 1
            for box in boxes:
                raw = (source_names or CORROSION_CLASSES)[box[0]] if source_names else CORROSION_CLASSES[box[0]]
                canonical = _normalise_class(raw)
                if canonical is None:
                    unmapped[raw] = unmapped.get(raw, 0) + 1
                    continue
                class_distribution[canonical] += 1
        except Exception as exc:
            malformed.append(f"{label}: {exc}")

    return {
        "dataset_root": str(root),
        "yaml": str(yaml_path),
        "images": len(images),
        "labels": len(images) - missing_labels,
        "missing_labels": missing_labels,
        "empty_labels": empty_labels,
        "malformed_annotations": malformed[:50],
        "malformed_count": len(malformed),
        "unmapped_classes": unmapped,
        "class_distribution": class_distribution,
        "source_classes": source_names or CORROSION_CLASSES,
        "classes": CORROSION_CLASSES,
        "valid": not malformed and missing_labels == 0 and len(images) > 0,
    }


def prepare_dataset(
    dataset_root: str,
    output_root: str = None,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
) -> dict:
    """Validate, clean, remap classes, deduplicate and split a corrosion dataset.

    Images with missing/malformed labels, duplicate content, or only-unmapped
    classes are excluded and reported. No images or annotations are generated.
    """
    import hashlib

    root = Path(dataset_root).resolve()
    destination = Path(output_root).resolve() if output_root else root / "prepared"
    if destination == root:
        raise ValueError("output_root must differ from dataset_root")
    if destination.exists():
        shutil.rmtree(destination)

    yaml_path, yaml_text, source_names = _load_dataset_yaml(root)
    roots = _dataset_image_roots(root, yaml_path, yaml_text)
    images = []
    for image_root in roots:
        for pattern in ("*.jpg", "*.jpeg", "*.png"):
            images.extend(image_root.rglob(pattern))
    images = sorted(set(images))

    records = []
    seen_hashes = set()
    dropped = {
        "missing_label": 0,
        "malformed": 0,
        "duplicate": 0,
        "no_supported_class": 0,
    }

    for image in images:
        label = _find_label(image)
        if not label:
            dropped["missing_label"] += 1
            continue
        try:
            boxes = _parse_label(label, source_names, canonical=False)
        except Exception:
            dropped["malformed"] += 1
            continue
        mapped = []
        for box in boxes:
            raw = (source_names or CORROSION_CLASSES)[box[0]] if source_names else CORROSION_CLASSES[box[0]]
            canonical = _normalise_class(raw)
            if canonical is None:
                continue
            mapped.append([CORROSION_CLASSES.index(canonical), *box[1:]])
        if not mapped:
            dropped["no_supported_class"] += 1
            continue
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if digest in seen_hashes:
            dropped["duplicate"] += 1
            continue
        seen_hashes.add(digest)
        records.append((image, label, mapped))

    groups = {}
    for image, _label, _boxes in records:
        key = image.parent.name or "root"
        groups.setdefault(key, []).append(image)
    group_names = sorted(groups.keys())
    split_map = _assign_splits(group_names, group_names, val_ratio, test_ratio, seed)

    for split in ("train", "valid", "test"):
        (destination / split / "images").mkdir(parents=True, exist_ok=True)
        (destination / split / "labels").mkdir(parents=True, exist_ok=True)

    counts = {"train": 0, "valid": 0, "test": 0}
    for image, label, mapped in records:
        group = image.parent.name or "root"
        split = split_map.get(group, "train")
        target_stem = f"{hashlib.sha1(str(image).encode()).hexdigest()[:16]}"
        shutil.copy2(image, destination / split / "images" / f"{target_stem}{image.suffix}")
        lines = [" ".join(str(value) for value in box) for box in mapped]
        (destination / split / "labels" / f"{target_stem}.txt").write_text("\n".join(lines), encoding="utf-8")
        counts[split] += 1

    yaml_body = ["names:"]
    for index, name in enumerate(CORROSION_CLASSES):
        yaml_body.append(f"  {index}: {name}")
    yaml_body.append("nc: %d" % len(CORROSION_CLASSES))
    yaml_body.append("train: train/images")
    yaml_body.append("val: valid/images")
    yaml_body.append("test: test/images")
    (destination / "data.yaml").write_text("\n".join(yaml_body) + "\n", encoding="utf-8")

    report = validate_dataset(str(destination))
    return {
        "dataset_root": str(destination),
        "yaml": str(destination / "data.yaml"),
        "source_root": str(root),
        "counts": counts,
        "dropped": dropped,
        "classes": CORROSION_CLASSES,
        "validation": report,
    }


def train(
    dataset_root: str,
    version: str = "1",
    epochs: int = 50,
    imgsz: int = 640,
    batch: int = 8,
    prepared_root: str = None,
    seed: int = 42,
    device: str = None,
) -> dict:
    """Train the corrosion model on a genuine prepared dataset."""
    from ultralytics import YOLO

    root = Path(dataset_root).resolve()
    if prepared_root:
        prepared = {"dataset_root": str(Path(prepared_root).resolve())}
    elif (root / "data.yaml").exists() and (root / "train").exists():
        prepared = {"dataset_root": str(root)}
    else:
        prepared = prepare_dataset(dataset_root, seed=seed)

    report = validate_dataset(prepared["dataset_root"])
    if not report["valid"]:
        raise RuntimeError("Prepared corrosion dataset failed validation; training was not started")

    dataset_record = register_dataset(prepared["dataset_root"])
    base_weights = str(BACKEND_DIR / "yolov8n.pt")
    model = YOLO(base_weights)
    run_name = f"{MODEL_ID}-v{version}"
    kwargs = {
        "data": str(Path(prepared["dataset_root"]) / "data.yaml"),
        "epochs": epochs,
        "imgsz": imgsz,
        "batch": batch,
        "project": str(MODEL_DIR),
        "name": run_name,
        "exist_ok": True,
        "seed": seed,
    }
    if device:
        kwargs["device"] = device
    results = model.train(**kwargs)
    trained = Path(results.save_dir) / "weights" / "best.pt"
    if not trained.exists():
        raise RuntimeError("Training completed without producing best.pt")

    stable = MODEL_DIR / MODEL_ID / f"v{version}" / "best.pt"
    stable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(trained, stable)

    conn = get_db_connection()
    conn.execute(
        "UPDATE ai_models SET status='AVAILABLE', weights_path=?, active_version=?, classes_json=?, updated_at=? WHERE id=?",
        (
            f"{MODEL_ID}/v{version}/best.pt",
            version,
            json.dumps({index: name for index, name in enumerate(CORROSION_CLASSES)}),
            now_iso(),
            MODEL_ID,
        ),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO model_versions (id, model_id, version, weights_path, dataset_id, metrics_json, status, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'COMPLETED', ?, ?)
        """,
        (
            f"MV-{MODEL_ID}-{version}",
            MODEL_ID,
            version,
            f"{MODEL_ID}/v{version}/best.pt",
            dataset_record["dataset_id"],
            json.dumps({}),
            "Corrosion model trained on a genuine labelled dataset",
            now_iso(),
        ),
    )
    conn.commit()
    conn.close()
    return {
        "model_id": MODEL_ID,
        "version": version,
        "weights": str(stable),
        "dataset_id": dataset_record["dataset_id"],
        "validation": report,
    }


def evaluate(weights_path: str, dataset_root: str, split: str = "test") -> dict:
    """Independent evaluation producing precision, recall, mAP50 and mAP50-95."""
    from ultralytics import YOLO

    model = YOLO(weights_path)
    metrics = model.val(
        data=str(Path(dataset_root) / "data.yaml"),
        split=split,
        imgsz=640,
    )
    box = metrics.box
    per_class = {}
    names = metrics.names if hasattr(metrics, "names") else {}
    for index, (p, r, map50, map5095) in enumerate(
        zip(box.p, box.r, box.map50, box.map)
    ):
        name = names.get(index, str(index)) if isinstance(names, dict) else str(index)
        per_class[name] = {
            "precision": float(p),
            "recall": float(r),
            "map50": float(map50),
            "map50_95": float(map5095),
        }
    return {
        "weights": str(weights_path),
        "dataset_root": str(dataset_root),
        "split": split,
        "precision": float(box.mp),
        "recall": float(box.mr),
        "map50": float(box.map50),
        "map50_95": float(box.map),
        "per_class": per_class,
        "classes": CORROSION_CLASSES,
    }


def main():
    parser = argparse.ArgumentParser(description="IGL pipeline corrosion dataset/training utility")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--dataset", required=True)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--dataset", required=True)
    p_prepare.add_argument("--output", default=None)
    p_prepare.add_argument("--val-ratio", type=float, default=0.15)
    p_prepare.add_argument("--test-ratio", type=float, default=0.15)
    p_prepare.add_argument("--seed", type=int, default=42)

    p_register = sub.add_parser("register")
    p_register.add_argument("--dataset", required=True)

    p_train = sub.add_parser("train")
    p_train.add_argument("--dataset", required=True)
    p_train.add_argument("--version", default="1")
    p_train.add_argument("--epochs", type=int, default=50)
    p_train.add_argument("--imgsz", type=int, default=640)
    p_train.add_argument("--batch", type=int, default=8)
    p_train.add_argument("--prepared-root", default=None)
    p_train.add_argument("--seed", type=int, default=42)
    p_train.add_argument("--device", default=None)

    p_eval = sub.add_parser("evaluate")
    p_eval.add_argument("--weights", required=True)
    p_eval.add_argument("--dataset", required=True)
    p_eval.add_argument("--split", default="test")

    args = parser.parse_args()

    if args.command == "validate":
        payload = validate_dataset(args.dataset)
    elif args.command == "prepare":
        payload = prepare_dataset(
            args.dataset,
            output_root=args.output,
            val_ratio=args.val_ratio,
            test_ratio=args.test_ratio,
            seed=args.seed,
        )
    elif args.command == "register":
        payload = register_dataset(args.dataset)
    elif args.command == "train":
        payload = train(
            args.dataset,
            version=args.version,
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            prepared_root=args.prepared_root,
            seed=args.seed,
            device=args.device,
        )
    else:
        payload = evaluate(args.weights, args.dataset, split=args.split)

    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())