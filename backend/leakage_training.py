#!/usr/bin/env python
"""Water pipeline leakage model preparation, training and evaluation.

This script never invents dataset content, labels, weights or metrics. It requires a
genuine YOLO-format dataset export and an installed Ultralytics runtime.
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import BACKEND_DIR, MODEL_DIR
from database import get_db_connection, now_iso

LEAKAGE_CLASSES = [
    "Water Dripping",
    "Water Spraying",
    "Visible Water Flow",
    "Pipe Joint Leakage",
    "Water Accumulation",
    "Normal/Dry Pipeline",
]

DATASET_CARD = {
    "name": "IGL water pipeline leakage",
    "source": "Roboflow Universe water leakage / pipeline leakage exports",
    "license": "CC BY 4.0 (verify each exported dataset card)",
    "classes": LEAKAGE_CLASSES,
    "status": "NOT_DOWNLOADED",
    "note": "No dataset files are bundled. Export a genuine dataset and point --dataset-root at it.",
}


CLASS_ALIASES = {
    "water dripping": 0,
    "water drip": 0,
    "dripping": 0,
    "drip": 0,
    "water spraying": 1,
    "water spray": 1,
    "spraying": 1,
    "spray": 1,
    "visible water flow": 2,
    "water flow": 2,
    "flowing water": 2,
    "flow": 2,
    "pipe joint leakage": 3,
    "joint leakage": 3,
    "pipe leakage": 3,
    "pipeline leakage": 3,
    "leakage": 3,
    "leak": 3,
    "pipe leak": 3,
    "water accumulation": 4,
    "accumulation": 4,
    "standing water": 4,
    "puddle": 4,
    "normal/dry pipeline": 5,
    "normal pipeline": 5,
    "dry pipeline": 5,
    "normal": 5,
    "dry": 5,
    "no leak": 5,
}


def _parse_names(yaml_text: str):
    names = []
    in_names = False
    for raw_line in yaml_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("names:"):
            rest = line.split(":", 1)[1].strip()
            if rest.startswith("[") and rest.endswith("]"):
                names = [value.strip().strip("'\"") for value in rest[1:-1].split(",") if value.strip()]
            elif rest.startswith("{") and rest.endswith("}"):
                pairs = []
                for item in rest[1:-1].split(","):
                    if ":" in item:
                        pairs.append(item.split(":", 1)[1].strip().strip("'\""))
                names = pairs
            elif rest:
                names = [rest.strip("'\"")]
            else:
                in_names = True
            continue
        if in_names:
            if line.startswith("- "):
                names.append(line[2:].strip().strip("'\""))
            elif ":" in line and line.split(":", 1)[0].strip().isdigit():
                names.append(line.split(":", 1)[1].strip().strip("'\""))
            elif not raw_line.startswith((" ", "\t")):
                break
    return names


def _load_dataset_yaml(dataset_root: Path):
    yaml_path = dataset_root / "data.yaml"
    if not yaml_path.exists():
        yaml_path = dataset_root / "dataset.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"data.yaml not found under {dataset_root}")
    text = yaml_path.read_text(encoding="utf-8")
    return yaml_path, text, _parse_names(text)


def _dataset_image_roots(dataset_root: Path, yaml_path: Path, yaml_text: str):
    roots = []
    for line in yaml_text.splitlines():
        stripped = line.strip()
        for key in ("train", "val", "valid", "test"):
            if stripped.startswith(f"{key}:"):
                value = stripped.split(":", 1)[1].strip().strip("'\"")
                if value and not value.startswith("["):
                    path = Path(value)
                    if not path.is_absolute():
                        path = (yaml_path.parent / path).resolve()
                    if path.exists():
                        roots.append(path)
    if not roots:
        roots = [dataset_root]
    return roots


def _find_label(image_path: Path):
    candidates = [image_path.with_suffix(".txt")]
    if image_path.parent.name.lower() in {"images", "image"}:
        candidates.append(image_path.parent.parent / "labels" / f"{image_path.stem}.txt")
    candidates.append(image_path.parent / "labels" / f"{image_path.stem}.txt")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _parse_label(label_path: Path, source_names, class_map=None, canonical=True, drop_unmapped=False):
    names = source_names or LEAKAGE_CLASSES
    id_map = None
    if canonical:
        id_map = {}
        mapping = class_map or {}
        for class_id, source_name in enumerate(names):
            normalized = str(source_name).strip().lower()
            mapped = mapping.get(source_name, mapping.get(normalized))
            if mapped is None:
                mapped = mapping.get(str(class_id), mapping.get(class_id))
            if mapped is None:
                mapped = CLASS_ALIASES.get(normalized)
            if mapped is None and drop_unmapped:
                id_map[class_id] = None
                continue
            if mapped is None:
                raise ValueError(f"Dataset class '{source_name}' cannot be mapped to the six leakage classes")
            id_map[class_id] = int(mapped)
    boxes = []
    for line_number, raw_line in enumerate(label_path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            raise ValueError(f"expected at least 5 YOLO fields, found {len(parts)} at line {line_number}")
        class_id = int(float(parts[0]))
        values = [float(value) for value in parts[1:]]
        if len(parts) == 5:
            center_x, center_y, width, height = values
        elif len(values) % 2 == 0 and len(values) >= 6:
            xs = values[0::2]
            ys = values[1::2]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            center_x = (min_x + max_x) / 2.0
            center_y = (min_y + max_y) / 2.0
            width = max_x - min_x
            height = max_y - min_y
        else:
            raise ValueError(f"unsupported YOLO label layout with {len(parts)} fields at line {line_number}")
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError(f"normalized coordinate outside 0..1 at line {line_number}")
        if width <= 0.0 or height <= 0.0:
            raise ValueError(f"zero-area bounding box at line {line_number}")
        if (
            center_x - width / 2.0 < -0.0001
            or center_x + width / 2.0 > 1.0001
            or center_y - height / 2.0 < -0.0001
            or center_y + height / 2.0 > 1.0001
        ):
            raise ValueError(f"bounding box exceeds image bounds at line {line_number}")
        if canonical:
            if class_id not in id_map:
                raise ValueError(f"class id {class_id} is outside the dataset class list")
            mapped_class = id_map[class_id]
            if mapped_class is None:
                continue
            class_id = mapped_class
        elif class_id < 0 or class_id >= len(names):
            raise ValueError(f"class id {class_id} is outside the dataset class list")
        boxes.append((class_id, center_x, center_y, width, height))
    return boxes


def validate_dataset(dataset_root: str) -> dict:
    root = Path(dataset_root)
    if not root.exists():
        raise FileNotFoundError(f"Dataset root not found: {root}")
    yaml_path, yaml_text, source_names = _load_dataset_yaml(root)
    roots = _dataset_image_roots(root, yaml_path, yaml_text)
    images = []
    for image_root in roots:
        images.extend(image_root.rglob("*.jpg"))
        images.extend(image_root.rglob("*.jpeg"))
        images.extend(image_root.rglob("*.png"))
    images = sorted(set(images))
    malformed = []
    missing_labels = 0
    class_distribution = {name: 0 for name in (source_names or LEAKAGE_CLASSES)}
    for image in images:
        label = _find_label(image)
        if not label:
            missing_labels += 1
            continue
        try:
            boxes = _parse_label(label, source_names, canonical=False)
            for box in boxes:
                class_name = (source_names or LEAKAGE_CLASSES)[box[0]]
                class_distribution[class_name] = class_distribution.get(class_name, 0) + 1
        except Exception as exc:
            malformed.append(f"{label}: {exc}")
    return {
        "dataset_root": str(root),
        "yaml": str(yaml_path),
        "images": len(images),
        "labels": len(images) - missing_labels,
        "missing_labels": missing_labels,
        "malformed_annotations": malformed[:50],
        "malformed_count": len(malformed),
        "class_distribution": class_distribution,
        "source_classes": source_names or LEAKAGE_CLASSES,
        "classes": source_names or LEAKAGE_CLASSES,
        "valid": not malformed and missing_labels == 0 and len(images) > 0,
    }


def prepare_dataset(
    dataset_root: str,
    output_root: str = None,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
    class_map: dict = None,
) -> dict:
    """Validate, clean, deduplicate and split a genuine YOLO dataset.

    Invalid annotations and missing labels are removed. Images are copied into
    train/val/test directories with canonical six-class labels. No images or
    annotations are generated.
    """
    root = Path(dataset_root).resolve()
    destination = Path(output_root).resolve() if output_root else root / "prepared"
    if destination == root:
        raise ValueError("output_root must be different from dataset_root")
    yaml_path, yaml_text, source_names = _load_dataset_yaml(root)
    roots = _dataset_image_roots(root, yaml_path, yaml_text)
    images = []
    for image_root in roots:
        images.extend(image_root.rglob("*.jpg"))
        images.extend(image_root.rglob("*.jpeg"))
        images.extend(image_root.rglob("*.png"))
    images = sorted(set(images))
    records = []
    seen_hashes = set()
    invalid = []
    missing = 0
    duplicates = 0
    for image in images:
        label = _find_label(image)
        if not label:
            missing += 1
            invalid.append(f"{image}: missing label")
            continue
        try:
            boxes = _parse_label(label, source_names, class_map=class_map, canonical=True)
        except Exception as exc:
            invalid.append(f"{label}: {exc}")
            continue
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if digest in seen_hashes:
            duplicates += 1
            continue
        seen_hashes.add(digest)
        records.append({"image": image, "label": label, "boxes": boxes})
    if not records:
        raise RuntimeError("No valid annotated images were found; dataset preparation stopped")
    random.Random(seed).shuffle(records)
    count = len(records)
    val_count = min(max(1, int(count * val_ratio)), max(0, count - 2))
    test_count = min(max(1, int(count * test_ratio)), max(0, count - val_count - 1))
    train_count = count - val_count - test_count
    splits = {
        "train": records[:train_count],
        "val": records[train_count:train_count + val_count],
        "test": records[train_count + val_count:],
    }
    if destination.exists():
        for split_name in splits:
            shutil.rmtree(destination / split_name / "images", ignore_errors=True)
            shutil.rmtree(destination / split_name / "labels", ignore_errors=True)
    class_distribution = {name: 0 for name in LEAKAGE_CLASSES}
    split_counts = {}
    for split_name, split_records in splits.items():
        split_counts[split_name] = len(split_records)
        (destination / split_name / "images").mkdir(parents=True, exist_ok=True)
        (destination / split_name / "labels").mkdir(parents=True, exist_ok=True)
        for record in split_records:
            image_target = destination / split_name / "images" / record["image"].name
            label_target = destination / split_name / "labels" / f"{record['image'].stem}.txt"
            shutil.copy2(record["image"], image_target)
            lines = [f"{class_id} {x:.6f} {y:.6f} {w:.6f} {h:.6f}" for class_id, x, y, w, h in record["boxes"]]
            label_target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            for class_id, *_ in record["boxes"]:
                class_distribution[LEAKAGE_CLASSES[class_id]] += 1
    prepared_yaml = destination / "data.yaml"
    prepared_yaml.write_text(
        "\n".join(
            [
                f"path: {destination.as_posix()}",
                "train: train/images",
                "val: val/images",
                "test: test/images",
                "names:",
                *[f"  {index}: {name}" for index, name in enumerate(LEAKAGE_CLASSES)],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    report = {
        "source_root": str(root),
        "dataset_root": str(destination),
        "yaml": str(prepared_yaml),
        "total_images": count,
        "valid_images": count,
        "duplicates_removed": duplicates,
        "missing_labels": missing,
        "invalid_annotations_removed": invalid[:100],
        "invalid_count": len(invalid),
        "split_counts": split_counts,
        "class_distribution": class_distribution,
        "classes": LEAKAGE_CLASSES,
        "seed": seed,
        "valid": count > 0,
    }
    (destination / "dataset_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def register_dataset(dataset_root: str, dataset_id: str = None) -> dict:
    report = validate_dataset(dataset_root)
    dataset_id = dataset_id or f"DS-{uuid.uuid4().hex[:8].upper()}"
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO dataset_metadata (id, name, source, license, version, image_count, classes_json, status, local_path, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            image_count = excluded.image_count,
            local_path = excluded.local_path,
            metadata_json = excluded.metadata_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            dataset_id,
            DATASET_CARD["name"],
            DATASET_CARD["source"],
            DATASET_CARD["license"],
            "1",
            report["images"],
            json.dumps(LEAKAGE_CLASSES),
            "READY" if report["valid"] else "INVALID",
            str(Path(dataset_root).resolve()),
            json.dumps(report),
        ),
    )
    conn.commit()
    conn.close()
    return {"dataset_id": dataset_id, **report}


def _source_group(dataset_name: str, image_path: Path) -> str:
    stem = image_path.stem
    for marker in ("_jpg.rf.", "_jpeg.rf.", "_png.rf."):
        if marker in stem:
            stem = stem.split(marker, 1)[0]
            break
    for marker in ("_mp4-", "_mp4_", "_frame", "_img", "_image"):
        if marker in stem:
            stem = stem.split(marker, 1)[0]
            break
    return f"{dataset_name}:{stem or image_path.stem}"


def _assign_splits(group_names, groups, val_ratio, test_ratio, seed):
    total_images = sum(groups[group]["images"] for group in group_names)
    target_val = max(1, int(total_images * val_ratio))
    target_test = max(1, int(total_images * test_ratio))
    shuffled = sorted(
        group_names,
        key=lambda group: hashlib.sha1(f"{seed}:{group}".encode("utf-8")).hexdigest(),
    )
    assignments = {}
    current = {"train": 0, "val": 0, "test": 0}
    split_classes = {"train": set(), "val": set(), "test": set()}

    def class_set(group_name):
        return set(groups[group_name]["classes"])

    def assign(group_name, split_name):
        assignments[group_name] = split_name
        current[split_name] += groups[group_name]["images"]
        split_classes[split_name].update(class_set(group_name))

    sources = sorted({source for group in group_names for source in groups[group]["sources"]})
    for split_name, target in (("val", target_val), ("test", target_test)):
        for source in sources:
            if current[split_name] >= target:
                break
            candidates = [
                group for group in shuffled
                if group not in assignments and source in groups[group]["sources"]
            ]
            if candidates:
                chosen = min(candidates, key=lambda group: groups[group]["images"])
                assign(chosen, split_name)

    for split_name in ("test", "val"):
        for class_name in LEAKAGE_CLASSES:
            if class_name in split_classes[split_name]:
                continue
            candidates = [
                group for group in shuffled
                if group not in assignments and class_name in class_set(group)
            ]
            if candidates:
                chosen = min(candidates, key=lambda group: groups[group]["images"])
                assign(chosen, split_name)

    toggle = 0
    for group_name in shuffled:
        if group_name in assignments:
            continue
        split_name = "val" if toggle % 2 == 0 else "test"
        other = "test" if split_name == "val" else "val"
        target = target_val if split_name == "val" else target_test
        other_target = target_test if split_name == "val" else target_val
        if current[split_name] + groups[group_name]["images"] <= target * 1.25 or current[other] >= other_target * 1.25:
            assign(group_name, split_name)
        elif current[other] + groups[group_name]["images"] <= other_target * 1.25:
            assign(group_name, other)
        else:
            assign(group_name, "train")
        toggle += 1

    for group_name in shuffled:
        if group_name not in assignments:
            assign(group_name, "train")
    return assignments


def combine_datasets(sources, output_root: str, val_ratio: float = 0.15, test_ratio: float = 0.15, seed: int = 42) -> dict:
    """Combine genuine datasets into one canonical YOLO detection dataset.

    Each source provides a root and a class_map. Classes not present in class_map
    are dropped rather than silently relabeled. Images without any mapped boxes are
    discarded. Splits are assigned by source-video group to prevent frame leakage.
    """
    destination = Path(output_root).resolve()
    if destination.exists():
        for split_name in ("train", "val", "test"):
            shutil.rmtree(destination / split_name / "images", ignore_errors=True)
            shutil.rmtree(destination / split_name / "labels", ignore_errors=True)
    records = []
    seen_hashes = set()
    duplicates = 0
    missing = 0
    invalid = 0
    source_summaries = []
    for source in sources:
        name = source["name"]
        root = Path(source["root"]).resolve()
        class_map = source.get("class_map") or {}
        yaml_path, yaml_text, source_names = _load_dataset_yaml(root)
        image_roots = _dataset_image_roots(root, yaml_path, yaml_text)
        images = []
        for image_root in image_roots:
            images.extend(image_root.rglob("*.jpg"))
            images.extend(image_root.rglob("*.jpeg"))
            images.extend(image_root.rglob("*.png"))
        images = sorted(set(images))
        source_count = 0
        source_missing = 0
        source_invalid = 0
        source_duplicates = 0
        for image_path in images:
            label = _find_label(image_path)
            if not label:
                missing += 1
                source_missing += 1
                continue
            try:
                boxes = _parse_label(label, source_names, class_map=class_map, canonical=True, drop_unmapped=True)
            except Exception:
                invalid += 1
                source_invalid += 1
                continue
            if not boxes:
                continue
            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            if digest in seen_hashes:
                duplicates += 1
                source_duplicates += 1
                continue
            seen_hashes.add(digest)
            records.append(
                {
                    "image": image_path,
                    "source": name,
                    "group": _source_group(name, image_path),
                    "boxes": boxes,
                }
            )
            source_count += 1
        source_summaries.append(
            {
                "name": name,
                "root": str(root),
                "images_used": source_count,
                "missing_labels": source_missing,
                "invalid_labels": source_invalid,
                "duplicates_removed": source_duplicates,
                "class_map": class_map,
                "source_classes": source_names,
            }
        )
    if not records:
        raise RuntimeError("No valid annotated images were found across the provided sources")
    groups = {}
    for record in records:
        group = groups.setdefault(record["group"], {"boxes": [], "images": 0, "sources": set(), "classes": set()})
        group["boxes"].extend(record["boxes"])
        group["images"] += 1
        group["sources"].add(record["source"])
        group["classes"].update(LEAKAGE_CLASSES[box[0]] for box in record["boxes"])
    assignments = _assign_splits(list(groups.keys()), groups, val_ratio, test_ratio, seed)
    class_distribution = {name: 0 for name in LEAKAGE_CLASSES}
    split_counts = {"train": 0, "val": 0, "test": 0}
    source_split_counts = {}
    for record in records:
        split_name = assignments[record["group"]]
        split_counts[split_name] += 1
        source_split_counts.setdefault(record["source"], {"train": 0, "val": 0, "test": 0})
        source_split_counts[record["source"]][split_name] += 1
        images_dir = destination / split_name / "images"
        labels_dir = destination / split_name / "labels"
        images_dir.mkdir(parents=True, exist_ok=True)
        labels_dir.mkdir(parents=True, exist_ok=True)
        counter = split_counts[split_name]
        suffix = record["image"].suffix.lower()
        image_target = images_dir / f"{record['source'][:16]}_{counter:06d}{suffix}"
        label_target = labels_dir / f"{record['source'][:16]}_{counter:06d}.txt"
        shutil.copy2(record["image"], image_target)
        lines = [
            f"{class_id} {center_x:.6f} {center_y:.6f} {width:.6f} {height:.6f}"
            for class_id, center_x, center_y, width, height in record["boxes"]
        ]
        label_target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        for box in record["boxes"]:
            class_distribution[LEAKAGE_CLASSES[box[0]]] += 1
    prepared_yaml = destination / "data.yaml"
    prepared_yaml.write_text(
        "\n".join(
            [
                f"path: {destination.as_posix()}",
                "train: train/images",
                "val: val/images",
                "test: test/images",
                "names:",
                *[f"  {index}: {name}" for index, name in enumerate(LEAKAGE_CLASSES)],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    report = {
        "dataset_root": str(destination),
        "yaml": str(prepared_yaml),
        "sources": source_summaries,
        "total_images": len(records),
        "duplicates_removed": duplicates,
        "missing_labels": missing,
        "invalid_labels": invalid,
        "split_counts": split_counts,
        "source_split_counts": source_split_counts,
        "class_distribution": class_distribution,
        "classes": LEAKAGE_CLASSES,
        "seed": seed,
        "split_by_source_group": True,
        "valid": all(split_counts.values()) and all(class_distribution.values()),
    }
    (destination / "dataset_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def oversample_dataset(
    dataset_root: str,
    output_root: str,
    classes: list = None,
    factor: int = 3,
    seed: int = 42,
) -> dict:
    """Duplicate existing training images that contain selected classes.

    This does not generate images or annotations. Validation and test splits are
    copied unchanged. It is intended only to reduce class imbalance for training.
    """
    root = Path(dataset_root).resolve()
    destination = Path(output_root).resolve()
    if destination == root:
        raise ValueError("output_root must be different from dataset_root")
    if not _is_prepared_dataset(root):
        raise ValueError("dataset_root must be a prepared train/val/test dataset")
    target_classes = set(classes or [])
    yaml_path, yaml_text, names = _load_dataset_yaml(root)
    for split_name in ("train", "val", "test"):
        target_dir = destination / split_name
        shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        if split_name != "train":
            shutil.copytree(root / split_name / "images", target_dir / "images")
            shutil.copytree(root / split_name / "labels", target_dir / "labels")
            continue
        (target_dir / "images").mkdir(parents=True, exist_ok=True)
        (target_dir / "labels").mkdir(parents=True, exist_ok=True)
        train_images = sorted((root / "train" / "images").glob("*.jpg")) + sorted((root / "train" / "images").glob("*.png"))
        records = []
        for image_path in train_images:
            label = _find_label(image_path)
            if not label:
                continue
            boxes = _parse_label(label, names, canonical=False)
            contains_target = any(names[box[0]] in target_classes for box in boxes) if target_classes else False
            copies = max(1, int(factor)) if contains_target else 1
            records.append((image_path, label, copies))
        random.Random(seed).shuffle(records)
        counter = 0
        for image_path, label, copies in records:
            for copy_index in range(copies):
                counter += 1
                suffix = image_path.suffix.lower()
                image_target = target_dir / "images" / f"balanced_{counter:06d}{suffix}"
                label_target = target_dir / "labels" / f"balanced_{counter:06d}.txt"
                shutil.copy2(image_path, image_target)
                shutil.copy2(label, label_target)
    prepared_yaml = destination / "data.yaml"
    prepared_yaml.write_text(
        "\n".join(
            [
                f"path: {destination.as_posix()}",
                "train: train/images",
                "val: val/images",
                "test: test/images",
                "names:",
                *[f"  {index}: {name}" for index, name in enumerate(names)],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    train_count = len(list((destination / "train" / "images").glob("*")))
    report = {
        "source_root": str(root),
        "dataset_root": str(destination),
        "yaml": str(prepared_yaml),
        "oversampled_classes": sorted(target_classes),
        "factor": factor,
        "train_images": train_count,
        "val_images": len(list((destination / "val" / "images").glob("*"))),
        "test_images": len(list((destination / "test" / "images").glob("*"))),
        "split_by_source_group_preserved": True,
        "valid": train_count > 0,
    }
    (destination / "oversample_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _is_prepared_dataset(dataset_root: Path) -> bool:
    yaml_path = dataset_root / "data.yaml"
    if not yaml_path.exists():
        return False
    try:
        _, _, names = _load_dataset_yaml(dataset_root)
    except Exception:
        return False
    return names == LEAKAGE_CLASSES and (dataset_root / "train" / "images").exists() and (dataset_root / "val" / "images").exists()


def train(
    dataset_root: str,
    model_id: str = "model-water-leakage",
    version: str = "1",
    epochs: int = 50,
    imgsz: int = 640,
    batch: int = 8,
    prepared_root: str = None,
    class_map: dict = None,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
    resume: bool = False,
    resume_checkpoint: str = None,
    device: str = None,
    workers: int = None,
) -> dict:
    from ultralytics import YOLO

    root = Path(dataset_root).resolve()
    if prepared_root:
        prepared = {"dataset_root": str(Path(prepared_root).resolve()), "yaml": str(Path(prepared_root).resolve() / "data.yaml")}
    elif _is_prepared_dataset(root):
        prepared = {"dataset_root": str(root), "yaml": str(root / "data.yaml")}
    else:
        prepared = prepare_dataset(
            dataset_root,
            val_ratio=val_ratio,
            test_ratio=test_ratio,
            seed=seed,
            class_map=class_map,
        )
    report = validate_dataset(prepared["dataset_root"])
    if not report["valid"]:
        raise RuntimeError("Prepared dataset validation failed; training was not started")
    yaml_path = report["yaml"]
    dataset_record = register_dataset(prepared["dataset_root"])
    dataset_id = dataset_record["dataset_id"]
    base_weights = resume_checkpoint or os.getenv("LEAKAGE_BASE_WEIGHTS", str(BACKEND_DIR / "yolov8n.pt"))
    model = YOLO(base_weights)
    run_name = f"{model_id}-v{version}"
    train_kwargs = {
        "data": yaml_path,
        "epochs": epochs,
        "imgsz": imgsz,
        "batch": batch,
        "project": str(MODEL_DIR),
        "name": run_name,
        "exist_ok": True,
    }
    if device:
        train_kwargs["device"] = device
    if workers:
        train_kwargs["workers"] = workers
    if resume:
        results = model.train(resume=True, **train_kwargs)
    else:
        results = model.train(**train_kwargs)
    trained_weights = Path(results.save_dir) / "weights" / "best.pt"
    if not trained_weights.exists():
        raise RuntimeError("Training completed without producing best.pt")
    stable_weights = MODEL_DIR / model_id / f"v{version}" / "best.pt"
    stable_weights.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(trained_weights, stable_weights)
    relative_weights = f"{model_id}/v{version}/best.pt"
    conn = get_db_connection()
    previous_row = conn.execute("SELECT metrics_json FROM ai_models WHERE id = ?", (model_id,)).fetchone()
    previous_metrics = {}
    if previous_row:
        try:
            previous_metrics = json.loads(previous_row["metrics_json"] or "{}")
        except Exception:
            previous_metrics = {}
    metrics = {
        "version": version,
        "dataset": prepared,
        "dataset_validation": report,
        "dataset_id": dataset_id,
        "epochs": epochs,
        "imgsz": imgsz,
        "batch": batch,
        "trained_weights": relative_weights,
        "absolute_trained_weights": str(stable_weights),
        "results": results.results_dict if hasattr(results, "results_dict") else {},
        "trained_at": now_iso(),
        "previous_metrics": previous_metrics,
    }
    conn.execute(
        "UPDATE ai_models SET status = 'AVAILABLE', weights_path = ?, active_version = ?, classes_json = ?, metrics_json = ?, updated_at = ? WHERE id = ?",
        (relative_weights, version, json.dumps(LEAKAGE_CLASSES), json.dumps(metrics, default=str), now_iso(), model_id),
    )
    conn.execute(
        """
        INSERT INTO model_versions (id, model_id, version, weights_path, dataset_id, metrics_json, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, 'COMPLETED', ?)
        """,
        (
            f"MV-{uuid.uuid4().hex[:8].upper()}",
            model_id,
            version,
            relative_weights,
            dataset_id,
            json.dumps(metrics, default=str),
            f"Trained by leakage_training.py version {version}",
        ),
    )
    conn.commit()
    conn.close()
    from inference_service import inference_service

    inference_service.invalidate_model(model_id)
    metrics["weights"] = relative_weights
    return metrics


def evaluate(weights_path: str, dataset_root: str, split: str = "test", model_id: str = None) -> dict:
    from ultralytics import YOLO

    root = Path(dataset_root).resolve()
    if not _is_prepared_dataset(root):
        prepared = prepare_dataset(dataset_root)
        root = Path(prepared["dataset_root"])
    report = validate_dataset(str(root))
    if not report["valid"]:
        raise RuntimeError("Dataset validation failed; evaluation was not started")
    if split == "val" and not (root / "val" / "images").exists():
        split = "test"
    model = YOLO(weights_path)
    metrics = model.val(data=report["yaml"], split=split)
    result = {
        "weights": weights_path,
        "dataset": report,
        "split": split,
        "metrics": metrics.results_dict if hasattr(metrics, "results_dict") else {},
        "evaluated_at": now_iso(),
    }
    if model_id:
        conn = get_db_connection()
        row = conn.execute("SELECT metrics_json FROM ai_models WHERE id = ?", (model_id,)).fetchone()
        existing = {}
        if row:
            try:
                existing = json.loads(row["metrics_json"] or "{}")
            except Exception:
                existing = {}
        existing["evaluation"] = result
        conn.execute(
            "UPDATE ai_models SET metrics_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(existing, default=str), now_iso(), model_id),
        )
        conn.commit()
        conn.close()
    return result


def evaluate_video(
    weights_path: str,
    video_path: str,
    conf: float = 0.25,
    sample_fps: float = 0.0,
    max_frames: int = 300,
    output_path: str = None,
) -> dict:
    """Run genuine recorded-video inference and write an annotated output video."""
    import cv2
    from ultralytics import YOLO

    source = Path(video_path)
    if not source.exists():
        raise FileNotFoundError(f"Video not found: {source}")
    model = YOLO(weights_path)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError("Video could not be decoded")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    interval = max(1, round(source_fps / sample_fps)) if sample_fps and sample_fps > 0 else 1
    output_fps = sample_fps if sample_fps and sample_fps > 0 else (source_fps or 1.0)
    output_path = Path(output_path) if output_path else Path(MODEL_DIR) / "video_evaluations" / f"{source.stem}_annotated.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), output_fps, (width, height))
    detections = 0
    frames_processed = 0
    inference_time = 0.0
    class_counts = {}
    frame_index = 0
    while frames_processed < max_frames:
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        if frame_index % interval == 0:
            started = time.time()
            results = model.predict(source=frame, conf=conf, save=False, verbose=False)
            inference_time += time.time() - started
            annotated = results[0].plot()
            writer.write(annotated)
            frames_processed += 1
            if results[0].boxes is not None:
                for box in results[0].boxes:
                    class_name = model.names.get(int(box.cls[0].item()), str(int(box.cls[0].item())))
                    class_counts[class_name] = class_counts.get(class_name, 0) + 1
                    detections += 1
        frame_index += 1
    capture.release()
    writer.release()
    result = {
        "weights": weights_path,
        "video": str(source),
        "output_video": str(output_path),
        "frames_processed": frames_processed,
        "frames_total": total_frames,
        "detections": detections,
        "class_counts": class_counts,
        "inference_seconds": round(inference_time, 3),
        "inference_fps": round(frames_processed / inference_time, 2) if inference_time > 0 else 0.0,
        "source_fps": source_fps,
        "sample_fps": sample_fps or source_fps,
        "evaluated_at": now_iso(),
    }
    output_path.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description="IGL water leakage model pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a YOLO-format dataset export")
    validate.add_argument("--dataset-root", required=True)

    register = subparsers.add_parser("register", help="Register a validated dataset in the model database")
    register.add_argument("--dataset-root", required=True)
    register.add_argument("--dataset-id")

    prepare_parser = subparsers.add_parser("prepare", help="Validate, clean, deduplicate and split a YOLO dataset")
    prepare_parser.add_argument("--dataset-root", required=True)
    prepare_parser.add_argument("--output-root")
    prepare_parser.add_argument("--val-ratio", type=float, default=0.15)
    prepare_parser.add_argument("--test-ratio", type=float, default=0.15)
    prepare_parser.add_argument("--seed", type=int, default=42)
    prepare_parser.add_argument("--class-map", help="JSON object mapping source class names to canonical class IDs")

    combine_parser = subparsers.add_parser("combine", help="Combine multiple genuine datasets with class mapping")
    combine_parser.add_argument("--config", required=True, help="JSON file containing a sources array")
    combine_parser.add_argument("--output-root", required=True)
    combine_parser.add_argument("--val-ratio", type=float, default=0.15)
    combine_parser.add_argument("--test-ratio", type=float, default=0.15)
    combine_parser.add_argument("--seed", type=int, default=42)

    oversample_parser = subparsers.add_parser("oversample", help="Duplicate training images containing weak classes")
    oversample_parser.add_argument("--dataset-root", required=True)
    oversample_parser.add_argument("--output-root", required=True)
    oversample_parser.add_argument("--classes", required=True, help="Comma-separated canonical class names")
    oversample_parser.add_argument("--factor", type=int, default=3)
    oversample_parser.add_argument("--seed", type=int, default=42)

    train_parser = subparsers.add_parser("train", help="Train a custom YOLO leakage model")
    train_parser.add_argument("--dataset-root", required=True)
    train_parser.add_argument("--prepared-root")
    train_parser.add_argument("--model-id", default="model-water-leakage")
    train_parser.add_argument("--version", default="1")
    train_parser.add_argument("--epochs", type=int, default=50)
    train_parser.add_argument("--imgsz", type=int, default=640)
    train_parser.add_argument("--batch", type=int, default=8)
    train_parser.add_argument("--val-ratio", type=float, default=0.15)
    train_parser.add_argument("--test-ratio", type=float, default=0.15)
    train_parser.add_argument("--seed", type=int, default=42)
    train_parser.add_argument("--class-map", help="JSON object mapping source class names to canonical class IDs")
    train_parser.add_argument("--resume", action="store_true")
    train_parser.add_argument("--resume-checkpoint")
    train_parser.add_argument("--device")
    train_parser.add_argument("--workers", type=int)

    eval_parser = subparsers.add_parser("eval", help="Evaluate a trained model on a dataset split")
    eval_parser.add_argument("--weights", required=True)
    eval_parser.add_argument("--dataset-root", required=True)
    eval_parser.add_argument("--split", default="test")
    eval_parser.add_argument("--model-id", help="Register evaluation metrics against this model ID")

    video_parser = subparsers.add_parser("video", help="Run recorded-video inference and write annotated output")
    video_parser.add_argument("--weights", required=True)
    video_parser.add_argument("--video", required=True)
    video_parser.add_argument("--conf", type=float, default=0.25)
    video_parser.add_argument("--sample-fps", type=float, default=0.0)
    video_parser.add_argument("--max-frames", type=int, default=300)
    video_parser.add_argument("--output")

    args = parser.parse_args()
    class_map = json.loads(args.class_map) if getattr(args, "class_map", None) else None
    if args.command == "validate":
        result = validate_dataset(args.dataset_root)
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 2
    if args.command == "prepare":
        result = prepare_dataset(
            args.dataset_root,
            output_root=args.output_root,
            val_ratio=args.val_ratio,
            test_ratio=args.test_ratio,
            seed=args.seed,
            resume=args.resume,
            resume_checkpoint=args.resume_checkpoint,
            device=args.device,
            workers=args.workers,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 2
    if args.command == "combine":
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"Configuration file not found: {config_path}", file=sys.stderr)
            return 2
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        result = combine_datasets(
            config_data.get("sources", []),
            output_root=args.output_root,
            val_ratio=args.val_ratio,
            test_ratio=args.test_ratio,
            seed=args.seed,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 2
    if args.command == "oversample":
        result = oversample_dataset(
            args.dataset_root,
            output_root=args.output_root,
            classes=[value.strip() for value in args.classes.split(",") if value.strip()],
            factor=args.factor,
            seed=args.seed,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 2
    if args.command == "register":
        result = register_dataset(args.dataset_root, args.dataset_id)
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 2
    if args.command == "train":
        result = train(
            args.dataset_root,
            model_id=args.model_id,
            version=args.version,
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            prepared_root=args.prepared_root,
            class_map=class_map,
            val_ratio=args.val_ratio,
            test_ratio=args.test_ratio,
            seed=args.seed,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0
    if args.command == "eval":
        result = evaluate(args.weights, args.dataset_root, args.split, args.model_id)
        print(json.dumps(result, indent=2, default=str))
        return 0
    if args.command == "video":
        result = evaluate_video(
            args.weights,
            args.video,
            conf=args.conf,
            sample_fps=args.sample_fps,
            max_frames=args.max_frames,
            output_path=args.output,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())