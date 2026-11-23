import base64
import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from camera_manager import camera_manager
from config import ALLOWED_ORIGINS, ENABLE_AUTH, EVIDENCE_DIR, MAX_UPLOAD_BYTES, REPORTS_DIR
from database import get_db_connection, log_audit, now_iso
from event_engine import event_engine
from inference_service import inference_service
from leakage_training import evaluate_video, prepare_dataset, validate_dataset
from notification_service import notification_status, notify, recent_deliveries
from reports import generate_report, get_report, list_reports
from security import (
    audit,
    client_ip,
    create_access_token,
    decrypt_secret,
    encrypt_secret,
    get_current_user,
    get_optional_user,
    hash_password,
    rate_limit,
    require_roles,
    revoke_token,
    verify_password,
)

router = APIRouter()


def _body(payload):
    return payload or {}


def _safe_camera_url(source_url: str) -> str:
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(source_url or "")
    if parsed.username or parsed.password:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return source_url or ""


def _json_loads(value, default):
    try:
        return json.loads(value or "{}")
    except Exception:
        return default


@router.post("/auth/login", dependencies=[Depends(rate_limit(20, 60))])
def login(payload: dict = Body(...)):
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not verify_password(password, row["password_hash"]):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(row["id"], row["username"], row["role"])
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), row["id"]))
    conn.commit()
    conn.close()
    audit(user={"username": row["username"]}, action="auth.login", resource_type="session", resource_id=row["id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": row["id"], "username": row["username"], "role": row["role"], "email": row["email"]},
    }


@router.get("/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": user}


@router.post("/auth/logout")
def logout(request: Request, user: dict = Depends(get_optional_user)):
    """Revoke the presented token so it cannot be reused (server-side logout)."""
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        token = request.query_params.get("token", "")
    if token:
        revoke_token(token)
    audit(user=user, action="auth.logout", resource_type="session", resource_id=user.get("sub"))
    return {"status": "LOGGED_OUT"}


@router.get("/cameras/{camera_id}")
def get_camera(camera_id: str, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Camera not found")
    item = dict(row)
    item.pop("credentials_json", None)
    item["has_credentials"] = bool(row["credentials_json"] and row["credentials_json"] not in {"{}", "null"})
    item["config"] = _json_loads(item.get("config_json"), {})
    return item


@router.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str, user: dict = Depends(require_roles("admin"))):
    conn = get_db_connection()
    row = conn.execute("SELECT id FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Camera not found")
    camera_manager.stop(camera_id)
    conn.execute("DELETE FROM monitoring_zones WHERE camera_id = ?", (camera_id,))
    conn.execute("DELETE FROM restricted_zones WHERE camera_id = ?", (camera_id,))
    conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
    conn.commit()
    conn.close()
    audit(user=user, action="camera.delete", resource_type="camera", resource_id=camera_id)
    return {"status": "DELETED", "camera_id": camera_id}


@router.post("/cameras/{camera_id}/test")
def test_current_camera(camera_id: str, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Camera not found")
    config = _json_loads(row["config_json"], {})
    credentials = _json_loads(row["credentials_json"], {})
    try:
        username = decrypt_secret(credentials.get("username", ""))
        password = decrypt_secret(credentials.get("password", ""))
    except Exception:
        username = password = ""
    from urllib.parse import urlparse, urlunparse

    source_url = row["source_url"] or ""
    if username or password:
        parsed = urlparse(source_url)
        host = parsed.hostname or ""
        netloc = host
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        source_url = urlunparse((parsed.scheme, f"{username}:{password}@{netloc}", parsed.path, parsed.params, parsed.query, parsed.fragment))
    options = {
        "width": config.get("width", 1920),
        "height": config.get("height", 1080),
        "fps": config.get("fps", 25),
        "timeout_ms": row["connection_timeout_ms"] or 8000,
        "device_index": config.get("device_index", 0),
    }
    result = camera_manager.test_source(row["source_type"], source_url, options)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Camera connection test failed"))
    audit(user=user, action="camera.test", resource_type="camera", resource_id=camera_id)
    return result


@router.get("/cameras/{camera_id}/health")
def camera_health(camera_id: str, user: dict = Depends(get_current_user)):
    health = camera_manager.health(camera_id)
    if not health.get("status"):
        raise HTTPException(status_code=404, detail="Camera not found")
    return health


@router.post("/cameras/{camera_id}/start")
def start_camera(camera_id: str, user: dict = Depends(require_roles("admin", "operator"))):
    try:
        camera_manager.start(camera_id)
        audit(user=user, action="camera.start", resource_type="camera", resource_id=camera_id)
        return {"status": "STARTED", "camera_id": camera_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cameras/{camera_id}/stop")
def stop_camera(camera_id: str, user: dict = Depends(require_roles("admin", "operator"))):
    camera_manager.stop(camera_id)
    audit(user=user, action="camera.stop", resource_type="camera", resource_id=camera_id)
    return {"status": "STOPPED", "camera_id": camera_id}


@router.post("/cameras/test-source")
def test_camera_source(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    source_type = payload.get("source_type", "rtsp")
    source_url = payload.get("source_url", "")
    options = payload.get("options", {}) or {}
    if not source_url and source_type not in {"webcam", "mock"}:
        raise HTTPException(status_code=422, detail="source_url is required")
    result = camera_manager.test_source(source_type, source_url, options)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Camera connection test failed"))
    return result


@router.put("/cameras/{camera_id}/config")
def update_camera_config(camera_id: str, payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Camera not found")
    config = _json_loads(row["config_json"], {})
    if isinstance(payload.get("config"), dict):
        config.update(payload["config"])
    config.update({key: value for key, value in payload.items() if key not in {"config", "credentials", "username", "password", "source_url"}})
    if "source_url" in payload:
        conn.execute("UPDATE cameras SET source_url = ? WHERE id = ?", (_safe_camera_url(payload["source_url"]), camera_id))
    if payload.get("plant_location_id") is not None:
        conn.execute("UPDATE cameras SET plant_location_id = ? WHERE id = ?", (payload["plant_location_id"], camera_id))
    if payload.get("pipeline_id") is not None:
        conn.execute("UPDATE cameras SET pipeline_id = ? WHERE id = ?", (payload["pipeline_id"], camera_id))
    if payload.get("name"):
        conn.execute("UPDATE cameras SET name = ? WHERE id = ?", (payload["name"], camera_id))
    credentials = _json_loads(row["credentials_json"], {})
    username = payload.get("username", credentials.get("username", ""))
    password = payload.get("password", credentials.get("password", ""))
    if username or password:
        credentials = {
            "username": encrypt_secret(username),
            "password": encrypt_secret(password),
        }
    conn.execute(
        "UPDATE cameras SET config_json = ?, credentials_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config), json.dumps(credentials), now_iso(), camera_id),
    )
    conn.commit()
    conn.close()
    camera_manager.stop(camera_id)
    audit(user=user, action="camera.configure", resource_type="camera", resource_id=camera_id)
    return {"status": "CONFIGURED", "camera_id": camera_id, "config": config}


@router.get("/cameras/{camera_id}/frame.jpg")
def camera_frame(camera_id: str, user: dict = Depends(get_current_user)):
    try:
        _, jpeg = camera_manager.get_frame_and_jpeg(camera_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(content=jpeg, media_type="image/jpeg")


@router.get("/cameras/{camera_id}/stream.mjpg")
async def camera_stream(camera_id: str, user: dict = Depends(get_current_user)):
    boundary = "iglvisionguard"
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}

    def generator():
        while True:
            try:
                _, jpeg = camera_manager.get_frame_and_jpeg(camera_id, quality=80)
                yield b"--" + boundary.encode() + b"\r\n"
                yield b"Content-Type: image/jpeg\r\n"
                yield f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                yield jpeg
                yield b"\r\n"
                time.sleep(0.035)
            except Exception:
                break

    return StreamingResponse(generator(), media_type=f"multipart/x-mixed-replace; boundary={boundary}", headers=headers)


@router.get("/zones")
def list_zones(camera_id: str = None, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    if camera_id:
        rows = conn.execute("SELECT * FROM monitoring_zones WHERE camera_id = ? ORDER BY created_at", (camera_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM monitoring_zones ORDER BY created_at").fetchall()
    conn.close()
    zones = []
    for row in rows:
        item = dict(row)
        item["polygon_coords"] = _json_loads(item["polygon_coords"], [])
        item["model_ids"] = _json_loads(item["model_ids"], [])
        item["settings_json"] = _json_loads(item["settings_json"], {})
        zones.append(item)
    return zones


@router.post("/zones")
def create_zone(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    camera_id = payload.get("camera_id")
    polygon = payload.get("polygon_coords") or []
    if not camera_id or not isinstance(polygon, list) or len(polygon) < 3:
        raise HTTPException(status_code=422, detail="camera_id and at least three polygon points are required")
    zone_id = payload.get("id") or f"ZONE-{uuid.uuid4().hex[:8].upper()}"
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO monitoring_zones (id, camera_id, zone_name, zone_type, polygon_coords, color, model_ids, settings_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            camera_id = excluded.camera_id, zone_name = excluded.zone_name, zone_type = excluded.zone_type,
            polygon_coords = excluded.polygon_coords, color = excluded.color, model_ids = excluded.model_ids,
            settings_json = excluded.settings_json, updated_at = CURRENT_TIMESTAMP
        """,
        (
            zone_id,
            camera_id,
            payload.get("zone_name", "Monitoring Zone"),
            payload.get("zone_type", "MONITORING"),
            json.dumps(polygon),
            payload.get("color", "#0ea5e9"),
            json.dumps(payload.get("model_ids", [])),
            json.dumps(payload.get("settings", {})),
        ),
    )
    conn.commit()
    conn.close()
    audit(user=user, action="zone.save", resource_type="zone", resource_id=zone_id)
    return {"status": "SAVED", "zone_id": zone_id}


@router.delete("/zones/{zone_id}")
def delete_zone(zone_id: str, user: dict = Depends(require_roles("admin", "operator"))):
    conn = get_db_connection()
    conn.execute("DELETE FROM monitoring_zones WHERE id = ?", (zone_id,))
    conn.commit()
    conn.close()
    audit(user=user, action="zone.delete", resource_type="zone", resource_id=zone_id)
    return {"status": "DELETED", "zone_id": zone_id}


@router.get("/plant-locations")
def list_plant_locations(user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM plant_locations ORDER BY name").fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.post("/plant-locations")
def create_plant_location(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    location_id = payload.get("id") or f"LOC-{uuid.uuid4().hex[:8].upper()}"
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO plant_locations (id, name, code, description, metadata_json) VALUES (?, ?, ?, ?, ?)",
        (
            location_id,
            payload.get("name", "Plant Location"),
            payload.get("code", ""),
            payload.get("description", ""),
            json.dumps(payload.get("metadata", {})),
        ),
    )
    conn.commit()
    conn.close()
    audit(user=user, action="plant_location.create", resource_type="plant_location", resource_id=location_id)
    return {"status": "CREATED", "id": location_id}


@router.get("/pipelines")
def list_pipelines(plant_location_id: str = None, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    if plant_location_id:
        rows = conn.execute("SELECT * FROM pipelines WHERE plant_location_id = ? ORDER BY name", (plant_location_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM pipelines ORDER BY name").fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.post("/pipelines")
def create_pipeline(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    pipeline_id = payload.get("id") or f"PIPE-{uuid.uuid4().hex[:8].upper()}"
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO pipelines (id, name, plant_location_id, material, description, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
        (
            pipeline_id,
            payload.get("name", "Pipeline"),
            payload.get("plant_location_id"),
            payload.get("material", ""),
            payload.get("description", ""),
            json.dumps(payload.get("metadata", {})),
        ),
    )
    conn.commit()
    conn.close()
    audit(user=user, action="pipeline.create", resource_type="pipeline", resource_id=pipeline_id)
    return {"status": "CREATED", "id": pipeline_id}


@router.get("/models")
def list_models(user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM ai_models ORDER BY name").fetchall()
    conn.close()
    models = []
    for row in rows:
        item = dict(row)
        item["classes"] = _json_loads(item["classes_json"], {})
        item["metadata"] = _json_loads(item["metadata_json"], {})
        item["metrics"] = _json_loads(item["metrics_json"], {})
        models.append(item)
    return models


@router.get("/models/health")
def model_health(user: dict = Depends(get_current_user)):
    return {"models": inference_service.health()}


@router.get("/models/{model_id}/versions")
def model_versions(model_id: str, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT id, version, weights_path, dataset_id, metrics_json, status, notes, created_at
        FROM model_versions WHERE model_id = ? ORDER BY created_at
        """,
        (model_id,),
    ).fetchall()
    conn.close()
    if not rows:
        raise HTTPException(status_code=404, detail="Model versions not found")
    versions = []
    for row in rows:
        item = dict(row)
        item["metrics"] = _json_loads(item.pop("metrics_json"), {})
        versions.append(item)
    return versions


@router.post("/models/{model_id}/activate-version")
def activate_model_version(model_id: str, payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    """Activate (or roll back to) a specific registered model version.

    This is the model rollback path: point `ai_models.weights_path` and
    `active_version` at an existing `model_versions` row and reload the model.
    Only versions that are already registered are selectable.
    """
    version = str(payload.get("version") or "").strip()
    if not version:
        raise HTTPException(status_code=422, detail="version is required")
    conn = get_db_connection()
    model = conn.execute("SELECT * FROM ai_models WHERE id = ?", (model_id,)).fetchone()
    if not model:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")
    row = conn.execute(
        "SELECT id, version, weights_path, metrics_json, status FROM model_versions WHERE model_id = ? AND version = ?",
        (model_id, version),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Version {version} is not registered for this model")
    if not row["weights_path"]:
        conn.close()
        raise HTTPException(status_code=409, detail="That version has no weights path and cannot be activated")
    conn.execute(
        "UPDATE ai_models SET weights_path = ?, active_version = ?, metrics_json = ?, status = 'AVAILABLE', updated_at = ? WHERE id = ?",
        (row["weights_path"], version, row["metrics_json"], now_iso(), model_id),
    )
    conn.commit()
    conn.close()
    inference_service.invalidate_model(model_id)
    status = inference_service.reload_model(model_id)
    audit(
        user=user,
        action="model.activate_version",
        resource_type="model",
        resource_id=model_id,
        details={"version": version, "previous_version": model["active_version"]},
    )
    return {
        "status": "ACTIVATED",
        "model_id": model_id,
        "version": version,
        "previous_version": model["active_version"],
        "weights_path": row["weights_path"],
        "loaded": status.get("loaded"),
    }


@router.get("/models/{model_id}/evaluation")
def model_evaluation(model_id: str, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM ai_models WHERE id = ?", (model_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    metrics = _json_loads(row["metrics_json"], {})
    evaluation = metrics.get("evaluation", {})
    return {
        "model_id": model_id,
        "name": row["name"],
        "status": row["status"],
        "active_version": row["active_version"],
        "weights_path": row["weights_path"],
        "classes": _json_loads(row["classes_json"], {}),
        "test_metrics": evaluation.get("metrics", {}),
        "per_class": evaluation.get("per_class", []),
        "confusion_matrix": evaluation.get("confusion_matrix"),
        "error_analysis": evaluation.get("error_analysis"),
        "training": {
            "version": metrics.get("version"),
            "epochs": metrics.get("epochs"),
            "imgsz": metrics.get("imgsz"),
            "batch": metrics.get("batch"),
            "trained_at": metrics.get("trained_at"),
            "dataset_id": metrics.get("dataset_id"),
        },
    }


@router.get("/models/{model_id}/health")
def model_health_one(model_id: str, user: dict = Depends(get_current_user)):
    status = inference_service.model_status(model_id)
    if status.get("status") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="Model not found")
    return status


@router.post("/models/{model_id}/reload")
def reload_model(model_id: str, user: dict = Depends(require_roles("admin", "operator"))):
    conn = get_db_connection()
    row = conn.execute("SELECT id FROM ai_models WHERE id = ?", (model_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    status = inference_service.reload_model(model_id)
    audit(user=user, action="model.reload", resource_type="model", resource_id=model_id)
    return status


@router.post("/models/{model_id}/detect")
def model_detect(model_id: str, payload: dict = Body(...), user: dict = Depends(get_current_user)):
    image_base64 = payload.get("image_base64", "")
    if "," in image_base64:
        image_base64 = image_base64.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="image_base64 is invalid") from exc
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds upload limit")
    conf = float(payload.get("conf", 0.25))
    imgsz = int(payload.get("imgsz", 1280))
    return inference_service.detect_image(image_bytes, model_id, conf=conf, imgsz=imgsz)


@router.post("/models/{model_id}/evaluate-video")
async def evaluate_video_endpoint(
    model_id: str,
    file: UploadFile = File(...),
    conf: float = Form(0.25),
    sample_fps: float = Form(0.0),
    max_frames: int = Form(300),
    camera_id: str = Form(None),
    user: dict = Depends(require_roles("admin", "operator")),
):
    if inference_service.get_model(model_id) is None:
        raise HTTPException(status_code=409, detail="Model is not trained, deployable, or weights are missing")
    if camera_id:
        conn = get_db_connection()
        row = conn.execute("SELECT id FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Camera not found")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded video is empty")
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Video exceeds 200 MB limit")
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    if suffix.lower() not in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
        raise HTTPException(status_code=422, detail="Unsupported video format")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
        temporary.write(content)
        temporary_path = temporary.name
    try:
        weights = inference_service.weights_path(model_id)
        result = evaluate_video(
            weights,
            temporary_path,
            conf=conf,
            sample_fps=sample_fps,
            max_frames=max_frames,
        )
        events = []
        if camera_id:
            capture = cv2.VideoCapture(temporary_path)
            source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            interval = max(1, round(source_fps / sample_fps)) if sample_fps and sample_fps > 0 else 1
            frame_index = 0
            processed = 0
            while processed < max_frames:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                if frame_index % interval == 0:
                    ok, buffer = cv2.imencode(".jpg", frame)
                    if ok:
                        event_result = event_engine.process_image(
                            camera_id,
                            buffer.tobytes(),
                            model_id=model_id,
                            conf=conf,
                            temporal=True,
                        )
                        events.extend(event_result.get("events", []))
                        processed += 1
                frame_index += 1
            capture.release()
        result["camera_id"] = camera_id
        created_events = [event for event in events if event.get("status") == "CREATED"]
        result["events_created"] = len(created_events)
        result["duplicate_events_suppressed"] = len(events) - len(created_events)
        result["event_details"] = events[:100]
        audit(
            user=user,
            action="model.evaluate_video",
            resource_type="model",
            resource_id=model_id,
            details={"frames": result.get("frames_processed"), "detections": result.get("detections"), "camera_id": camera_id},
        )
        return result
    finally:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass


@router.get("/datasets")
def list_datasets(user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM dataset_metadata ORDER BY created_at DESC").fetchall()
    conn.close()
    datasets = []
    for row in rows:
        item = dict(row)
        item["classes"] = _json_loads(item["classes_json"], [])
        item["metadata"] = _json_loads(item["metadata_json"], {})
        datasets.append(item)
    return datasets


@router.post("/datasets/validate")
def validate_dataset_endpoint(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    root = payload.get("dataset_root", "")
    if not root:
        raise HTTPException(status_code=422, detail="dataset_root is required")
    return validate_dataset(root)


@router.post("/datasets/prepare")
def prepare_dataset_endpoint(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    root = payload.get("dataset_root", "")
    if not root:
        raise HTTPException(status_code=422, detail="dataset_root is required")
    try:
        result = prepare_dataset(
            root,
            output_root=payload.get("output_root"),
            val_ratio=float(payload.get("val_ratio", 0.15)),
            test_ratio=float(payload.get("test_ratio", 0.15)),
            seed=int(payload.get("seed", 42)),
            class_map=payload.get("class_map"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(user=user, action="dataset.prepare", resource_type="dataset", resource_id=result.get("dataset_root"), details=result)
    return result


@router.get("/events")
def list_events(limit: int = Query(100, ge=1, le=1000), event_type: str = None, status: str = None, user: dict = Depends(get_current_user)):
    return event_engine.recent_events(limit=limit, event_type=event_type, status=status)


@router.post("/events/process")
def process_event(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    camera_id = payload.get("camera_id", "")
    image_base64 = payload.get("image_base64", "")
    if not camera_id or not image_base64:
        raise HTTPException(status_code=422, detail="camera_id and image_base64 are required")
    if "," in image_base64:
        image_base64 = image_base64.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="image_base64 is invalid") from exc
    result = event_engine.process_image(
        camera_id,
        image_bytes,
        model_id=payload.get("model_id"),
        conf=payload.get("conf"),
        temporal=bool(payload.get("temporal", False)),
        zone_id=payload.get("zone_id"),
    )
    for event in result.get("events", []):
        if event.get("status") == "CREATED" and event.get("alert_id"):
            conn = get_db_connection()
            alert = conn.execute("SELECT * FROM alerts WHERE id = ?", (event["alert_id"],)).fetchone()
            conn.close()
            if alert:
                event_data = {
                    "event_type": alert["event_type"],
                    "camera_id": alert["camera_id"],
                    "camera_name": alert["camera_name"],
                    "zone_name": alert["zone_name"],
                    "severity": alert["severity"],
                    "confidence": alert["confidence"],
                    "timestamp": alert["detected_at"],
                    "metadata": alert["metadata_json"],
                }
                notify(event["alert_id"], event.get("incident_id"), event_data, channels=["IN_APP"])
    return result


@router.get("/incidents/leakage")
def list_leakage_incidents(status: str = None, limit: int = Query(100, ge=1, le=1000), user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    query = "SELECT * FROM leakage_incidents"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY detected_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.put("/incidents/leakage/{incident_id}/review")
def review_leakage_incident(incident_id: str, payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    decision = (payload.get("decision") or "").upper()
    if decision not in {"CONFIRMED", "DISMISSED", "SUSPECTED", "POSSIBLE"}:
        raise HTTPException(status_code=422, detail="decision must be CONFIRMED, DISMISSED, SUSPECTED or POSSIBLE")
    conn = get_db_connection()
    incident = conn.execute("SELECT * FROM leakage_incidents WHERE id = ?", (incident_id,)).fetchone()
    if not incident:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")
    notes = payload.get("notes", "")
    conn.execute(
        "UPDATE leakage_incidents SET status = ?, reviewed_by = ?, reviewed_at = ?, review_notes = ?, updated_at = ? WHERE id = ?",
        (decision, user["username"], now_iso(), notes, now_iso(), incident_id),
    )
    if incident["event_id"]:
        status = "RESOLVED" if decision in {"DISMISSED", "CONFIRMED"} else "SUSPECTED"
        conn.execute(
            "UPDATE detection_events SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), incident["event_id"]),
        )
        conn.execute(
            "UPDATE alerts SET status = ?, resolved_at = ?, resolved_by = ?, resolution_notes = ? WHERE event_id = ?",
            (status, now_iso(), user["username"], notes, incident["event_id"]),
        )
    conn.execute(
        """
        INSERT INTO user_reviews (id, event_id, incident_id, review_type, decision, notes, reviewer, is_false_positive, is_false_negative)
        VALUES (?, ?, ?, 'LEAKAGE', ?, ?, ?, ?, ?)
        """,
        (
            f"REV-{uuid.uuid4().hex[:8].upper()}",
            incident["event_id"],
            incident_id,
            decision,
            notes,
            user["username"],
            1 if decision == "DISMISSED" else 0,
            0,
        ),
    )
    conn.commit()
    conn.close()
    audit(user=user, action="incident.leakage.review", resource_type="leakage_incident", resource_id=incident_id, details={"decision": decision})
    return {"status": "REVIEWED", "incident_id": incident_id, "decision": decision}


@router.get("/incidents/safety")
def list_safety_incidents(status: str = None, limit: int = Query(100, ge=1, le=1000), user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    query = "SELECT * FROM safety_incidents"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY detected_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.put("/incidents/safety/{incident_id}/review")
def review_safety_incident(incident_id: str, payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    decision = (payload.get("decision") or "").upper()
    if decision not in {"CONFIRMED", "DISMISSED", "SUSPECTED"}:
        raise HTTPException(status_code=422, detail="decision must be CONFIRMED, DISMISSED or SUSPECTED")
    conn = get_db_connection()
    incident = conn.execute("SELECT * FROM safety_incidents WHERE id = ?", (incident_id,)).fetchone()
    if not incident:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")
    notes = payload.get("notes", "")
    conn.execute(
        "UPDATE safety_incidents SET status = ?, reviewed_by = ?, reviewed_at = ?, review_notes = ?, updated_at = ? WHERE id = ?",
        (decision, user["username"], now_iso(), notes, now_iso(), incident_id),
    )
    if incident["event_id"]:
        conn.execute("UPDATE detection_events SET status = ?, updated_at = ? WHERE id = ?", (decision, now_iso(), incident["event_id"]))
        conn.execute(
            "UPDATE alerts SET status = ?, resolved_at = ?, resolved_by = ?, resolution_notes = ? WHERE event_id = ?",
            (decision, now_iso(), user["username"], notes, incident["event_id"]),
        )
    conn.execute(
        """
        INSERT INTO user_reviews (id, event_id, incident_id, review_type, decision, notes, reviewer, is_false_positive, is_false_negative)
        VALUES (?, ?, ?, 'SAFETY', ?, ?, ?, ?, ?)
        """,
        (
            f"REV-{uuid.uuid4().hex[:8].upper()}",
            incident["event_id"],
            incident_id,
            decision,
            notes,
            user["username"],
            1 if decision == "DISMISSED" else 0,
            0,
        ),
    )
    conn.commit()
    conn.close()
    audit(user=user, action="incident.safety.review", resource_type="safety_incident", resource_id=incident_id, details={"decision": decision})
    return {"status": "REVIEWED", "incident_id": incident_id, "decision": decision}


@router.get("/evidence")
def list_evidence(limit: int = Query(100, ge=1, le=1000), user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM evidence_files ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM evidence_files WHERE id = ?", (evidence_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Evidence not found")
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Evidence file missing")
    return FileResponse(path, media_type=row["content_type"] or "image/jpeg")


@router.get("/notifications/status")
def get_notification_status(user: dict = Depends(get_current_user)):
    return notification_status()


@router.post("/notifications/test")
def test_notifications(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    channels = payload.get("channels") or ["IN_APP"]
    result = notify(
        alert_id=f"TEST-{uuid.uuid4().hex[:6].upper()}",
        incident_id=None,
        event_data={
            "event_type": "TEST_NOTIFICATION",
            "camera_name": payload.get("camera_name", "Test"),
            "zone_name": payload.get("zone_name", "Test Zone"),
            "confidence": payload.get("confidence", 1.0),
            "timestamp": now_iso(),
        },
        channels=channels,
    )
    return result


@router.get("/notifications/deliveries")
def notification_deliveries(limit: int = Query(100, ge=1, le=1000), user: dict = Depends(get_current_user)):
    return recent_deliveries(limit)


@router.post("/reports")
def create_report(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    report_type = payload.get("report_type", "daily")
    if report_type not in {"daily", "weekly", "monthly", "leakage", "safety", "camera_uptime", "alert_response", "model_performance"}:
        raise HTTPException(status_code=422, detail="Unsupported report_type")
    result = generate_report(
        report_type,
        payload.get("start"),
        payload.get("end"),
        generated_by=user["username"],
        format=payload.get("format", "json"),
    )
    audit(user=user, action="report.generate", resource_type="report", resource_id=result["report_id"], details={"report_type": report_type})
    return result


@router.get("/reports")
def reports(limit: int = Query(100, ge=1, le=1000), user: dict = Depends(get_current_user)):
    return list_reports(limit)


@router.get("/reports/{report_id}")
def report_detail(report_id: str, user: dict = Depends(get_current_user)):
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/reports/{report_id}/download")
def report_download(report_id: str, user: dict = Depends(get_current_user)):
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    path = Path(report["file_path"]) if report.get("file_path") else None
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Report file not generated")
    return FileResponse(path, filename=path.name)


@router.get("/audit-logs")
def audit_logs(limit: int = Query(100, ge=1, le=1000), user: dict = Depends(require_roles("admin"))):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.get("/users")
def users(user: dict = Depends(require_roles("admin"))):
    conn = get_db_connection()
    rows = conn.execute("SELECT id, username, email, role, is_active, created_at, last_login_at FROM users ORDER BY username").fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.post("/users")
def create_user(payload: dict = Body(...), user: dict = Depends(require_roles("admin"))):
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    if len(username) < 3 or len(password) < 8:
        raise HTTPException(status_code=422, detail="Username must be at least 3 characters and password at least 8")
    user_id = payload.get("id") or f"USR-{uuid.uuid4().hex[:8].upper()}"
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO users (id, username, email, password_hash, role, is_active) VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                username,
                payload.get("email", ""),
                hash_password(password),
                payload.get("role", "viewer"),
                1 if payload.get("is_active", True) else 0,
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Could not create user: {exc}") from exc
    conn.close()
    audit(user=user, action="user.create", resource_type="user", resource_id=user_id)
    return {"status": "CREATED", "id": user_id}


@router.put("/users/{user_id}")
def update_user(user_id: str, payload: dict = Body(...), user: dict = Depends(require_roles("admin"))):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    password_hash = row["password_hash"]
    if payload.get("password"):
        password_hash = hash_password(payload["password"])
    conn.execute(
        "UPDATE users SET username = ?, email = ?, password_hash = ?, role = ?, is_active = ? WHERE id = ?",
        (
            payload.get("username", row["username"]),
            payload.get("email", row["email"]),
            password_hash,
            payload.get("role", row["role"]),
            1 if payload.get("is_active", bool(row["is_active"])) else 0,
            user_id,
        ),
    )
    conn.commit()
    conn.close()
    audit(user=user, action="user.update", resource_type="user", resource_id=user_id)
    return {"status": "UPDATED", "id": user_id}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, user: dict = Depends(require_roles("admin"))):
    if user_id == user["sub"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    conn = get_db_connection()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    audit(user=user, action="user.delete", resource_type="user", resource_id=user_id)
    return {"status": "DELETED", "id": user_id}


@router.get("/settings")
def settings(user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    rows = conn.execute("SELECT key, value FROM app_settings ORDER BY key").fetchall()
    conn.close()
    result = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except Exception:
            result[row["key"]] = row["value"]
    return result


@router.put("/settings")
def update_settings(payload: dict = Body(...), user: dict = Depends(require_roles("admin"))):
    conn = get_db_connection()
    for key, value in payload.items():
        if any(part in key.lower() for part in {"password", "token", "secret", "key"}):
            continue
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), now_iso()),
        )
    conn.commit()
    conn.close()
    audit(user=user, action="settings.update", details={"keys": list(payload.keys())})
    return {"status": "UPDATED"}


@router.get("/system/health")
def system_health(user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    db_ok = True
    try:
        conn.execute("SELECT 1").fetchone()
    except Exception:
        db_ok = False
    camera_rows = conn.execute("SELECT status AS status, COUNT(*) AS count FROM cameras GROUP BY status").fetchall()
    conn.close()
    return {
        "status": "OK" if db_ok else "DEGRADED",
        "database": {"connected": db_ok, "path": os.getenv("DATABASE_PATH", "igl_safety.db")},
        "cameras": {row["status"]: row["count"] for row in camera_rows},
        "models": inference_service.health(),
        "notifications": notification_status(),
        "auth_enabled": ENABLE_AUTH,
        "storage": {
            "evidence_dir": str(EVIDENCE_DIR),
            "reports_dir": str(REPORTS_DIR),
            "evidence_exists": EVIDENCE_DIR.exists(),
            "reports_exists": REPORTS_DIR.exists(),
        },
        "timestamp": now_iso(),
    }


@router.post("/pipeline/corrosion/analyze-image")
async def analyze_pipeline_corrosion(
    file: UploadFile = File(...),
    conf: float = Form(0.25),
    camera_id: str = Form(None),
    user: dict = Depends(require_roles("admin", "operator")),
):
    """Run genuine corrosion inference on an uploaded pipeline image.

    Records a detection event and evidence for each accepted detection. Rust or
    corrosion appearance is never reported as proof of structural failure, and
    detections remain subject to human review.
    """
    from corrosion_training import CORROSION_CLASSES, MODEL_ID

    if inference_service.get_model(MODEL_ID) is None:
        raise HTTPException(
            status_code=409,
            detail="Corrosion model is not trained/deployed. No detections were produced.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded image is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded image exceeds size limit")

    result = inference_service.detect_image(content, MODEL_ID, conf=conf)
    if result.get("status") != "SUCCESS":
        raise HTTPException(status_code=422, detail=result.get("message", "Inference failed"))

    supported = set(CORROSION_CLASSES)
    created_events = []
    for detection in result.get("detections", []):
        class_name = (detection.get("class_name") or "").lower()
        if class_name not in supported:
            continue
        evidence_id = f"EVD-{uuid.uuid4().hex[:10].upper()}"
        suffix = Path(file.filename or "corrosion.jpg").suffix or ".jpg"
        evidence_path = Path(EVIDENCE_DIR) / f"{evidence_id}{suffix}"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_bytes(content)
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO evidence_files (id, camera_id, file_path, content_type, sha256) VALUES (?, ?, ?, ?, ?)",
            (
                evidence_id,
                camera_id,
                str(evidence_path),
                file.content_type or "image/jpeg",
                hashlib.sha256(content).hexdigest(),
            ),
        )
        event_id = f"EVT-COR-{uuid.uuid4().hex[:10].upper()}"
        conn.execute(
            """
            INSERT INTO detection_events (id, camera_id, model_id, event_type, severity, status, confidence, bbox_json, evidence_id, frame_timestamp)
            VALUES (?, ?, ?, 'PIPELINE_CORROSION', 'MEDIUM', 'NEW', ?, ?, ?, ?)
            """,
            (
                event_id,
                camera_id,
                MODEL_ID,
                float(detection.get("confidence", 0.0)),
                json.dumps(detection.get("bbox")),
                evidence_id,
                now_iso(),
            ),
        )
        conn.commit()
        conn.close()
        created_events.append(
            {
                "event_id": event_id,
                "class": class_name,
                "confidence": detection.get("confidence"),
                "bbox": detection.get("bbox"),
                "evidence_id": evidence_id,
                "timestamp": now_iso(),
                "review_required": True,
                "note": "Surface appearance only. Not proof of structural failure.",
            }
        )

    audit(
        user=user,
        action="pipeline.corrosion.analyze",
        resource_type="pipeline",
        resource_id=camera_id,
        details={"detections": len(created_events)},
    )
    return {
        "status": "SUCCESS",
        "model_id": MODEL_ID,
        "source_kind": "recorded_image",
        "detections_count": len(created_events),
        "events": created_events,
        "note": "Corrosion appearance is not structural confirmation; all detections require human review.",
    }


@router.get("/pipeline/corrosion/status")
def pipeline_corrosion_status(user: dict = Depends(get_current_user)):
    """Report corrosion model availability honestly (no fabricated readiness)."""
    from corrosion_training import CORROSION_CLASSES, MODEL_ID

    status = inference_service.model_status(MODEL_ID)
    return {
        "model_id": MODEL_ID,
        "status": status.get("status"),
        "loaded": bool(status.get("loaded")),
        "classes": CORROSION_CLASSES,
        "available": status.get("status") == "AVAILABLE",
        "note": "Rust/corrosion appearance is never treated as proof of structural failure.",
    }


@router.get("/monitoring/summary")
def monitoring_summary(user: dict = Depends(get_current_user)):
    """Operational monitoring snapshot: health, cameras, models, workers, database.

    Every value is derived from real state. Nothing is fabricated and no device
    is reported connected unless it genuinely is.
    """
    from camera_worker import camera_worker_manager

    conn = get_db_connection()
    try:
        camera_rows = conn.execute(
            "SELECT id, name, source_type, status, last_seen FROM cameras"
        ).fetchall()
        model_rows = conn.execute(
            "SELECT id, status, active_version, weights_path FROM ai_models"
        ).fetchall()
        workers = camera_worker_manager.status()
        counts = {
            "detection_events": conn.execute("SELECT COUNT(*) FROM detection_events").fetchone()[0],
            "alerts": conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
            "evidence_files": conn.execute("SELECT COUNT(*) FROM evidence_files").fetchone()[0],
            "audit_logs": conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0],
        }
    finally:
        conn.close()

    cameras = [dict(row) for row in camera_rows]
    online = [c for c in cameras if (c.get("status") or "").upper() == "ONLINE"]

    models = []
    for row in model_rows:
        item = dict(row)
        item["loaded"] = inference_service.get_model(item["id"]) is not None
        models.append(item)

    worker_rows = [w for w in workers]
    failed_workers = [w for w in worker_rows if w.get("last_error")]

    return {
        "application": {
            "status": "OK",
            "environment": os.getenv("ENVIRONMENT", "development"),
            "auth_enabled": ENABLE_AUTH,
        },
        "cameras": {
            "registered": len(cameras),
            "online": len(online),
            "offline": len(cameras) - len(online),
            "items": cameras,
        },
        "models": {
            "registered": len(models),
            "available": len([m for m in models if m["status"] == "AVAILABLE" and m["loaded"]]),
            "unavailable": len([m for m in models if m["status"] != "AVAILABLE"]),
            "items": models,
        },
        "workers": {
            "running": len([w for w in worker_rows if w.get("running")]),
            "with_errors": len(failed_workers),
            "items": worker_rows,
        },
        "database": counts,
        "note": "Values reflect actual persisted state; no fabricated metrics.",
    }


@router.get("/monitoring/health")
def monitoring_health():
    """Liveness/readiness probe used by monitors (complements /api/system/health)."""
    from database import get_db_connection

    checks = {}
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        checks["database"] = "UP"
    except Exception as exc:
        checks["database"] = f"DOWN: {exc}"

    try:
        model = inference_service.get_model("model-yolov8n")
        checks["primary_model"] = "UP" if model is not None else "NOT_LOADED"
    except Exception as exc:
        checks["primary_model"] = f"ERROR: {exc}"

    healthy = checks.get("database") == "UP"
    return {
        "status": "OK" if healthy else "DEGRADED",
        "checks": checks,
        "environment": os.getenv("ENVIRONMENT", "development"),
    }


@router.post("/uploads/evidence")
async def upload_evidence(file: UploadFile = File(...), user: dict = Depends(require_roles("admin", "operator"))):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds size limit")
    evidence_id = f"EVD-{uuid.uuid4().hex[:10].upper()}"
    suffix = Path(file.filename or "evidence.jpg").suffix or ".jpg"
    if suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=422, detail="Only JPG, PNG and WEBP evidence images are accepted")
    path = Path(EVIDENCE_DIR) / f"{evidence_id}{suffix}"
    path.write_bytes(content)
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO evidence_files (id, camera_id, file_path, content_type) VALUES (?, ?, ?, ?)",
        (evidence_id, None, str(path), file.content_type or "application/octet-stream"),
    )
    conn.commit()
    conn.close()
    audit(user=user, action="evidence.upload", resource_type="evidence", resource_id=evidence_id)
    return {"status": "UPLOADED", "evidence_id": evidence_id, "url": f"/api/evidence/{evidence_id}"}