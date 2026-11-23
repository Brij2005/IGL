import json
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

from leakage_training import _parse_label, prepare_dataset, validate_dataset


def _write_dataset(root: Path):
    (root / "train" / "images").mkdir(parents=True)
    (root / "train" / "labels").mkdir(parents=True)
    (root / "val" / "images").mkdir(parents=True)
    (root / "val" / "labels").mkdir(parents=True)
    (root / "data.yaml").write_text(
        "\n".join(
            [
                "path: " + root.as_posix(),
                "train: train/images",
                "val: val/images",
                "names:",
                "  0: leak",
                "  1: normal",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    image = np.full((48, 64, 3), (80, 80, 80), dtype=np.uint8)
    cv2.putText(image, "TEST", (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    records = [
        ("train", "leak_0", "0 0.1 0.1 0.2 0.2"),
        ("train", "leak_1", "0 0.2 0.2 0.2 0.2"),
        ("train", "normal_0", "1 0.1 0.1 0.2 0.2"),
        ("val", "leak_val", "0 0.3 0.3 0.2 0.2"),
        ("val", "normal_val", "1 0.3 0.3 0.2 0.2"),
        ("train", "malformed", "0 0.1 0.1"),
    ]
    for split, name, label in records:
        image_path = root / split / "images" / f"{name}.jpg"
        label_path = root / split / "labels" / f"{name}.txt"
        frame = image.copy()
        cv2.putText(frame, name, (5, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
        cv2.imwrite(str(image_path), frame)
        label_path.write_text(label + "\n", encoding="utf-8")
    duplicate_source = root / "train" / "images" / "leak_0.jpg"
    duplicate = root / "train" / "images" / "leak_duplicate.jpg"
    shutil.copy2(duplicate_source, duplicate)
    (root / "train" / "labels" / "leak_duplicate.txt").write_text("0 0.1 0.1 0.2 0.2\n", encoding="utf-8")
    return root


def test_validate_dataset_reports_malformed_and_missing():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = _write_dataset(Path(temp_dir) / "dataset")
        report = validate_dataset(str(root))
        assert report["images"] == 7
        assert report["malformed_count"] >= 1
        assert report["class_distribution"]["leak"] >= 1
        assert report["valid"] is False


def test_prepare_dataset_remaps_classes_deduplicates_and_splits():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = _write_dataset(Path(temp_dir) / "dataset")
        result = prepare_dataset(
            str(root),
            output_root=str(Path(temp_dir) / "prepared"),
            val_ratio=0.34,
            test_ratio=0.17,
            seed=7,
            class_map={"leak": 3, "normal": 5},
        )
        assert result["valid"] is True
        assert result["duplicates_removed"] >= 1
        assert result["invalid_count"] >= 1
        assert result["split_counts"]["train"] >= 1
        assert result["split_counts"]["val"] >= 1
        assert result["class_distribution"]["Pipe Joint Leakage"] >= 1
        assert result["class_distribution"]["Normal/Dry Pipeline"] >= 1
        prepared = Path(result["dataset_root"])
        names = (prepared / "data.yaml").read_text(encoding="utf-8")
        assert "Pipe Joint Leakage" in names
        assert (prepared / "train" / "images").exists()
        assert (prepared / "val" / "images").exists()
        assert (prepared / "test" / "images").exists()
        label_files = list((prepared / "train" / "labels").glob("*.txt"))
        assert label_files
        first_label = label_files[0].read_text(encoding="utf-8").strip()
        assert first_label.split()[0] in {"3", "5"}
        assert json.loads((prepared / "dataset_report.json").read_text(encoding="utf-8"))["valid"] is True


def test_prepare_dataset_rejects_unknown_source_class():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = _write_dataset(Path(temp_dir) / "dataset")
        yaml_path = root / "data.yaml"
        yaml_path.write_text(
            yaml_path.read_text(encoding="utf-8").replace("0: leak", "0: unknown").replace("1: normal", "1: normal"),
            encoding="utf-8",
        )
        try:
            prepare_dataset(str(root), output_root=str(Path(temp_dir) / "prepared"))
        except (ValueError, RuntimeError) as exc:
            assert "cannot be mapped" in str(exc) or "No valid annotated images" in str(exc)
        else:
            raise AssertionError("Unknown source class should fail canonical mapping")

def test_polygon_annotation_converts_to_bounding_box():
    with tempfile.TemporaryDirectory() as temp_dir:
        label = Path(temp_dir) / "polygon.txt"
        label.write_text("0 0.1 0.1 0.3 0.1 0.3 0.3 0.1 0.3\n", encoding="utf-8")
        boxes = _parse_label(label, ["leak"], canonical=False)
        assert len(boxes) == 1
        _, center_x, center_y, width, height = boxes[0]
        assert abs(center_x - 0.2) < 1e-6
        assert abs(center_y - 0.2) < 1e-6
        assert abs(width - 0.2) < 1e-6
        assert abs(height - 0.2) < 1e-6


def test_unmapped_classes_are_dropped_without_relabeling():
    with tempfile.TemporaryDirectory() as temp_dir:
        label = Path(temp_dir) / "mixed.txt"
        label.write_text("0 0.2 0.2 0.2 0.2\n1 0.4 0.4 0.2 0.2\n", encoding="utf-8")
        boxes = _parse_label(
            label,
            ["leak", "fire"],
            class_map={"leak": 3},
            canonical=True,
            drop_unmapped=True,
        )
        assert len(boxes) == 1
        assert boxes[0][0] == 3
