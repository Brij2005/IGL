"""Pipeline corrosion module tests.

Covers the ingestion/validation pipeline, honest model unavailability, and the
guarantee that non-corrosion classes are never relabelled as corrosion.

The tiny YOLO fixtures below exist ONLY to exercise dataset validation code in a
temporary directory. They are never registered as a dataset, never trained on,
and never presented as operational data.
"""

import base64
import os
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-corrosion-test-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")

import pytest
from fastapi.testclient import TestClient

from corrosion_training import (
    CORROSION_CLASSES,
    MODEL_ID,
    _normalise_class,
    validate_dataset,
)
from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _make_tiny_dataset(root: Path, class_names, annotations):
    """Create a minimal YOLO dataset purely to test validation logic."""
    images_dir = root / "train" / "images"
    labels_dir = root / "train" / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    (root / "data.yaml").write_text(
        "names:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(class_names)) + f"nc: {len(class_names)}\n",
        encoding="utf-8",
    )
    for index, boxes in enumerate(annotations):
        image = np.full((64, 64, 3), (60 + index * 5, 60, 60), dtype=np.uint8)
        cv2.imwrite(str(images_dir / f"img_{index}.jpg"), image)
        (labels_dir / f"img_{index}.txt").write_text(
            "\n".join(" ".join(str(v) for v in box) for box in boxes), encoding="utf-8"
        )
    return root


# --------------------------------------------------------------- taxonomy
def test_only_corrosion_classes_are_supported():
    assert "external_corrosion" in CORROSION_CLASSES
    assert "surface_rust" in CORROSION_CLASSES
    assert "normal_pipe_surface" in CORROSION_CLASSES
    assert len(CORROSION_CLASSES) == 5


def test_aliases_map_and_non_corrosion_classes_are_rejected():
    assert _normalise_class("Rust") == "surface_rust"
    assert _normalise_class("corrosion") == "external_corrosion"
    assert _normalise_class("no-corrosion") == "normal_pipe_surface"
    # Non-corrosion defects must NOT be relabelled as corrosion.
    assert _normalise_class("crack") is None
    assert _normalise_class("dent") is None
    assert _normalise_class("weld defect") is None


# ------------------------------------------------------------- validation
def test_validate_dataset_reports_unmapped_classes():
    root = TEST_ROOT / "mixed"
    _make_tiny_dataset(
        root,
        ["corrosion", "crack"],
        [[[0, 0.5, 0.5, 0.2, 0.2]], [[1, 0.1, 0.1, 0.2, 0.2]]],
    )
    report = validate_dataset(str(root))
    assert report["images"] == 2
    assert report["class_distribution"]["external_corrosion"] == 1
    # The crack annotation must be reported as unmapped, never as corrosion.
    assert "crack" in report["unmapped_classes"]
    assert report["class_distribution"]["surface_rust"] == 0


def test_validate_dataset_flags_missing_labels():
    root = TEST_ROOT / "missing"
    _make_tiny_dataset(root, ["corrosion"], [[[0, 0.5, 0.5, 0.2, 0.2]]])
    (root / "train" / "images" / "unlabelled.jpg").write_bytes(
        (root / "train" / "images" / "img_0.jpg").read_bytes()
    )
    report = validate_dataset(str(root))
    assert report["missing_labels"] >= 1
    assert report["valid"] is False


# --------------------------------------------------------- model honesty
def test_corrosion_model_is_not_trained(client):
    status = client.get("/api/pipeline/corrosion/status").json()
    assert status["model_id"] == MODEL_ID
    assert status["available"] is False
    assert status["loaded"] is False


def test_corrosion_analyze_rejects_when_model_unavailable(client):
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), (90, 90, 90), dtype=np.uint8))
    response = client.post(
        "/api/pipeline/corrosion/analyze-image",
        files={"file": ("pipe.jpg", buf.tobytes(), "image/jpeg")},
        data={"conf": "0.25"},
    )
    assert response.status_code == 409, "Unavailable model must fail honestly"
    assert "not trained" in response.json()["detail"].lower()


def test_corrosion_detect_returns_model_unavailable(client):
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), (90, 90, 90), dtype=np.uint8))
    body = client.post(
        f"/api/models/{MODEL_ID}/detect",
        json={"image_base64": base64.b64encode(buf.tobytes()).decode()},
    ).json()
    assert body["status"] == "MODEL_UNAVAILABLE"
    assert body.get("detections") == []


def test_no_fabricated_corrosion_events(client):
    from database import get_db_connection

    conn = get_db_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM detection_events WHERE event_type = 'PIPELINE_CORROSION'"
    ).fetchone()[0]
    conn.close()
    assert count == 0, "No corrosion events may exist without a trained model"


def test_corrosion_model_never_marked_available_without_weights(client):
    from database import get_db_connection

    conn = get_db_connection()
    row = conn.execute(
        "SELECT status, weights_path FROM ai_models WHERE id = ?", (MODEL_ID,)
    ).fetchone()
    conn.close()
    assert row["status"] != "AVAILABLE" or row["weights_path"], \
        "A model must never be AVAILABLE without weights"


def teardown_module(module):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)