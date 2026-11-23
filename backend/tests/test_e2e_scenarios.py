"""Phase 6 end-to-end scenario tests.

Each test drives a complete workflow through the real FastAPI app against an
isolated database. Genuine recorded media (data/videos/*.mp4) is used where
available; controlled detections are injected only where a specific object
class is required, and each such test states that explicitly.

No test asserts success by fabricating records: where a model is unavailable
or media lacks the target class, the test asserts the honest outcome (0 events,
MODEL_UNAVAILABLE, UNVERIFIED, ...).
"""

import base64
import os
import shutil
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-e2e-suite-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")
os.environ.setdefault("EVIDENCE_DIR", os.path.join(TEST_ROOT, "evidence"))
os.environ.setdefault("REPORTS_DIR", os.path.join(TEST_ROOT, "reports"))

import pytest
from fastapi.testclient import TestClient

from main import app

GENUINE_VIDEOS = [
    BACKEND_ROOT / "data" / "videos" / "water_leak_5.mp4",
    BACKEND_ROOT / "data" / "videos" / "water_leak_6.mp4",
]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client
    try:
        from camera_manager import camera_manager

        camera_manager.stop_all()
    except Exception:
        pass


def _img_bytes(color=(70, 100, 120)):
    ok, buf = cv2.imencode(".jpg", np.full((240, 320, 3), color, dtype=np.uint8))
    return base64.b64encode(buf.tobytes()).decode()


def _write_image(name, color=(70, 100, 120)):
    path = TEST_ROOT / name
    cv2.imwrite(str(path), np.full((240, 320, 3), color, dtype=np.uint8))
    return path


def _db_counts(**tables):
    from database import get_db_connection

    conn = get_db_connection()
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()


# ------------------------------------------------------ Test 1: General CCTV
def test_e2e_1_general_cctv_video_to_dashboard(client):
    """Video -> adapter -> worker -> YOLO -> tracking -> event -> evidence -> dashboard.

    Uses a genuine recorded clip. The clip contains no people and no gate is
    configured, so the honest expectation is: frames processed, zero events.
    """
    video = next((v for v in GENUINE_VIDEOS if v.exists()), None)
    if video is None:
        pytest.skip("No genuine recorded test clip available")

    response = client.post(
        "/api/security/analyze-video",
        files={"file": (video.name, video.read_bytes(), "video/mp4")},
        data={"source_kind": "cctv", "max_frames": "20"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["frames_processed"] > 0, "Video must be genuinely decoded"
    assert body["model_id"] == "model-yolov8n"
    assert body["model_status"] == "AVAILABLE"
    # No people in the clip and no gate configured -> no fabricated events.
    assert body["entry_count"] == 0
    assert body["exit_count"] == 0
    assert "recorded" in body["notes"].lower()

    # The analysis must be persisted and visible to the dashboard.
    analyses = client.get("/api/security/video-analyses").json()
    assert any(a["id"] == body["analysis_id"] for a in analyses)
    dashboard = client.get("/api/security/dashboard").json()
    assert dashboard["video_analyses"] >= 1


# --------------------------------------------- Test 2: Restricted zone alert
def test_e2e_2_restricted_zone_event_incident_alert_evidence(client):
    """Video/frame -> person detection -> zone validation -> event -> incident -> alert -> evidence.

    A controlled person detection is injected so the zone-validation path can
    be exercised deterministically; the event, incident, alert and evidence
    records are all produced by the real event engine.
    """
    from inference_service import inference_service

    image_path = _write_image("restricted_frame.jpg", color=(70, 90, 110))
    cam = client.post(
        "/api/cameras", json={"name": "E2E Restricted Cam", "source_type": "image", "source_url": str(image_path)}
    ).json()["camera"]["id"]
    client.post(
        "/api/zones",
        json={"camera_id": cam, "zone_name": "E2E Restricted", "zone_type": "RESTRICTED",
              "polygon_coords": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    )

    original = inference_service.detect_image
    inference_service.detect_image = lambda *a, **k: {
        "status": "SUCCESS",
        "detections": [{"class_name": "person", "confidence": 0.93, "bbox": [0.4, 0.4, 0.2, 0.3]}],
    }
    try:
        response = client.post(
            "/api/events/process",
            json={"camera_id": cam, "image_base64": _img_bytes(), "model_id": "model-yolov8n", "conf": 0.0},
        )
        events = response.json()["events"]
    finally:
        inference_service.detect_image = original

    created = [e for e in events if e.get("status") == "CREATED"]
    assert created, "A restricted-zone person must create an event"
    assert any(e["event_type"] == "RESTRICTED_ZONE" for e in created)
    assert any(e.get("alert_id") for e in created), "Alertable event must produce an alert"

    counts = _db_counts(detection_events="detection_events", alerts="alerts", evidence_files="evidence_files")
    assert counts["detection_events"] >= 1
    assert counts["alerts"] >= 1
    assert counts["evidence_files"] >= 1, "Evidence must be stored for the event"

    # The alert must be visible on the dashboard.
    dashboard = client.get("/api/dashboard/stats").json()
    assert dashboard["active_alerts"] >= 1


# ------------------------------------------------- Test 3: Water Leakage
def test_e2e_3_water_leakage_inference_and_persistence(client):
    """Recorded video -> leakage model -> detection -> DB -> evidence -> event.

    Uses a genuine leakage clip through the real evaluation endpoint. If the
    leakage model has no weights in this test database the endpoint must return
    an honest 409 ("model not deployable") rather than fabricating detections.
    """
    from inference_service import inference_service

    has_weights = inference_service.get_model("model-water-leakage") is not None

    video = next((v for v in GENUINE_VIDEOS if v.exists()), None)
    if video is None:
        pytest.skip("No genuine leakage clip available")

    response = client.post(
        "/api/models/model-water-leakage/evaluate-video",
        files={"file": (video.name, video.read_bytes(), "video/mp4")},
        data={"max_frames": "15"},
    )

    if has_weights:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["frames_processed"] > 0
        assert "detections_count" in body or "detections" in body
    else:
        # Honest unavailability: no fabricated leakage detections.
        assert response.status_code == 409, response.text
        assert "not trained" in response.json()["detail"].lower() or "weights" in response.json()["detail"].lower()


def test_e2e_3b_leakage_incidents_require_review(client):
    """Leakage incidents must not auto-confirm pipe leakage."""
    incidents = client.get("/api/incidents/leakage").json()
    assert isinstance(incidents, list)
    for incident in incidents:
        # An incident is a reviewable observation, not an automatic confirmation.
        assert "status" in incident
        assert incident.get("status") in {"OPEN", "ACKNOWLEDGED", "CLOSED", "RESOLVED"}


# ------------------------------------------- Test 4: Smart Entry & Exit
def test_e2e_4_entry_exit_tracking_access_record_review(client):
    """Recorded video -> person tracking -> direction -> access record -> human review."""
    from inference_service import inference_service
    from security_monitoring import security_service

    gate = {
        "gate_id": "GATE-E2E", "camera_id": "CAM-E2E-ENTRY",
        "entry_zone": {"x": 0.0, "y": 0.6, "w": 1.0, "h": 0.4},
        "exit_zone": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.4},
    }
    from database import get_db_connection
    import json as _json

    conn = get_db_connection()
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        ("security_gates", _json.dumps([gate]), "2026-11-22T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    security_service._tracker._last_zone.clear()
    security_service._event_engine.tracker.tracks.clear()

    original = inference_service.detect_image
    calls = {"n": 0}

    def fake_detect(image_bytes, model_id="model-yolov8n", conf=0.25, imgsz=1280):
        step = min(calls["n"], 6)
        cy = 0.2 + (0.6 * step / 6.0)
        calls["n"] += 1
        return {"status": "SUCCESS", "detections": [
            {"class_name": "person", "confidence": 0.9, "bbox": [0.4, cy - 0.1, 0.2, 0.2]}
        ]}

    inference_service.detect_image = fake_detect
    try:
        frames = [np.full((200, 200, 3), (60 + 5 * i, 60, 60), dtype=np.uint8) for i in range(8)]
        result = security_service.analyze_recorded_video(
            filename="e2e_entry_exit.mp4", frames=frames, source_kind="cctv", camera_id="CAM-E2E-ENTRY"
        )
    finally:
        inference_service.detect_image = original

    assert result["frames_processed"] == 8
    assert result["entry_count"] + result["exit_count"] >= 1

    records = client.get("/api/security/access-records").json()
    mine = [r for r in records if r["camera_id"] == "CAM-E2E-ENTRY"]
    assert mine, "Access records must be persisted"
    assert all(r["verification_status"] == "UNVERIFIED" for r in mine), "AI-only records must need review"
    assert all(r["evidence_id"] for r in mine), "Each record must carry evidence"

    # Human review resolves one of them.
    reviewed = client.put(
        f"/api/security/access-records/{mine[0]['id']}/review",
        json={"decision": "VERIFIED", "notes": "ID verified at gate"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["decision"] == "VERIFIED"

    updated = {r["id"]: r for r in client.get("/api/security/access-records").json()}
    assert updated[mine[0]["id"]]["verification_status"] == "VERIFIED"


# ------------------------------------------------ Test 5: Asset movement
def test_e2e_5_asset_register_gate_pass_movement(client):
    """Asset register -> gate pass -> RFID record -> movement verification -> event."""
    asset = client.post(
        "/api/security/assets",
        json={"asset_name": "E2E Pump", "asset_category": "EQUIPMENT", "rfid_tag": "RFID-E2E-1"},
    ).json()
    asset_id = asset["id"]

    # Verify from the register (AI detection is never the source of truth).
    verified = client.get("/api/security/assets/verify", params={"rfid": "RFID-E2E-1"}).json()
    assert verified["verified"] is True
    assert verified["asset"]["id"] == asset_id

    unknown = client.get("/api/security/assets/verify", params={"barcode": "NO-SUCH-BARCODE"}).json()
    assert unknown["verified"] is False

    # Movement without a gate pass is UNMATCHED (raises a review event).
    unmatched = client.post(
        f"/api/security/assets/{asset_id}/movements",
        json={"direction": "EXIT", "gate_id": "GATE-2", "verification_source": "RFID"},
    ).json()
    assert unmatched["verification_status"] == "UNMATCHED"

    # Issue a gate pass, link it, then the movement matches.
    gate_pass = client.post(
        "/api/security/gate-passes",
        json={"asset_id": asset_id, "issued_to_employee_id": "EMP-E2E", "purpose": "Repair"},
    ).json()
    client.put(f"/api/security/assets/{asset_id}", json={"gate_pass_id": gate_pass["id"]})

    matched = client.post(
        f"/api/security/assets/{asset_id}/movements",
        json={"direction": "EXIT", "gate_id": "GATE-2", "verification_source": "RFID"},
    ).json()
    assert matched["verification_status"] == "MATCHED"

    movements = client.get("/api/security/assets/movements").json()
    statuses = {m["id"]: m["verification_status"] for m in movements}
    assert statuses[unmatched["id"]] == "UNMATCHED"
    assert statuses[matched["id"]] == "MATCHED"

    events = client.get("/api/security/events").json()
    assert any(e["type"] == "UNMATCHED_ASSET_MOVEMENT" for e in events), \
        "Unmatched movement must surface as a review event"


# ------------------------------------------------------ Test 6: Thermal
def test_e2e_6_thermal_model_unavailable_and_calibration(client):
    """Thermal input -> thermal-compatible model -> honest MODEL_UNAVAILABLE -> DB."""
    health = client.get("/api/models/model-thermal-person/health").json()
    assert health["status"] == "NOT_TRAINED"
    assert health["loaded"] is False

    detect = client.post("/api/models/model-thermal-person/detect", json={"image_base64": _img_bytes()}).json()
    assert detect["status"] == "MODEL_UNAVAILABLE"
    assert detect.get("detections") == [], "An untrained model must never produce detections"

    # Thermal recorded analysis must also be honest.
    from database import get_db_connection

    conn = get_db_connection()
    conn.execute(
        "INSERT INTO cameras (id, name, source_type, source_url, status) VALUES ('CAM-THERMAL-E2E','T','thermal','x','ONLINE')"
    )
    conn.commit()
    conn.close()

    from security_monitoring import security_service

    frames = [np.full((120, 160, 3), (30, 30, 30), dtype=np.uint8) for _ in range(3)]
    result = security_service.analyze_recorded_video(
        filename="thermal.mp4", frames=frames, source_kind="thermal", camera_id="CAM-THERMAL-E2E"
    )
    assert result["model_id"] == "model-thermal-person"
    assert result["model_status"] == "NOT_TRAINED"
    assert result["detections_count"] == 0
    assert result["entry_count"] == 0 and result["exit_count"] == 0
    assert "not trained" in result["notes"].lower()

    # Calibration is enforced before temperature is stored.
    assert client.post(
        "/api/security/thermal/readings",
        json={"camera_id": "CAM-THERMAL-E2E", "temperature_c": 38.0, "threshold_c": 37.0, "calibrated": False},
    ).status_code == 422
    ok = client.post(
        "/api/security/thermal/readings",
        json={"camera_id": "CAM-THERMAL-E2E", "temperature_c": 38.0, "threshold_c": 37.0, "calibrated": True},
    )
    assert ok.status_code == 200
    assert ok.json()["threshold_exceeded"] is True

    readings = client.get("/api/security/thermal/readings").json()
    assert any(r["calibrated"] for r in readings)
    assert any(r["threshold_exceeded"] for r in readings)


# ------------------------------------------------- Test 7: Authentication
def test_e2e_7_authentication_role_and_audit(client):
    """Login -> role verification -> protected API -> authorized action -> audit log.

    This test runs in-process with auth enabled for the scenario only by using
    the security primitives directly, since the module-level client runs with
    ENABLE_AUTH=false (the development configuration).
    """
    from fastapi import HTTPException
    import security as sec

    # Password hashing is salted and verifiable.
    h1 = sec.hash_password("E2E-Pass-1!")
    h2 = sec.hash_password("E2E-Pass-1!")
    assert h1 != h2, "Salted hashing required"
    assert sec.verify_password("E2E-Pass-1!", h1)
    assert not sec.verify_password("wrong", h1)

    # Tokens carry role and are validated/revoked.
    token = sec.create_access_token("USR-E2E", "e2e", "viewer")
    payload = sec.decode_access_token(token)
    assert payload["role"] == "viewer"
    assert payload["username"] == "e2e"

    sec.revoke_token(token)
    with pytest.raises(HTTPException):
        sec.decode_access_token(token)

    with pytest.raises(HTTPException):
        sec.decode_access_token(token + "tampered")

    # Role enforcement helper.
    allowed = sec.require_roles("admin", "operator")
    with pytest.raises(HTTPException):
        # viewer must be rejected by an admin/operator dependency
        allowed(user={"role": "viewer"})
    assert allowed(user={"role": "operator"})["role"] == "operator"

    # Audit logging records the action.
    sec.audit(user={"username": "e2e"}, action="e2e.test", resource_type="test", resource_id="1")
    from database import get_db_connection

    conn = get_db_connection()
    row = conn.execute("SELECT action FROM audit_logs WHERE actor='e2e' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None and row[0] == "e2e.test"


def teardown_module(module):
    try:
        from camera_manager import camera_manager

        camera_manager.stop_all()
    except Exception:
        pass
    shutil.rmtree(TEST_ROOT, ignore_errors=True)