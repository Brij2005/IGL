"""API routes for the IGL Smart Entry & Exit Security module.

Mounted under /api by main.py. Reuses the existing database, inference service,
event engine and hardware adapters. No duplicate tables or services.
"""

import json
import os
import tempfile
from pathlib import Path

import cv2
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile

from config import MAX_UPLOAD_BYTES
from database import get_db_connection, log_audit, now_iso
from hardware_adapters import create_hardware_adapter, create_video_adapter
from inference_service import inference_service
from security import audit, get_current_user, require_roles
from security_monitoring import (
    CCTV_MODEL_ID,
    ENTRY,
    EXIT,
    THERMAL_MODEL_ID,
    security_service,
)

router = APIRouter()

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
MAX_VIDEO_BYTES = 200 * 1024 * 1024


def _decode_upload(content: bytes, suffix: str, max_bytes: int) -> bytes:
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds size limit")
    return content


def _sample_frames(path: str, sample_fps: float, max_frames: int):
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise HTTPException(status_code=422, detail="Video could not be decoded")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    interval = max(1, round(source_fps / sample_fps)) if sample_fps and sample_fps > 0 else 1
    frames = []
    index = 0
    while len(frames) < max_frames:
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        if index % interval == 0:
            frames.append(frame)
        index += 1
    capture.release()
    return frames


# --------------------------------------------------------------------- dashboard
@router.get("/security/dashboard")
def security_dashboard(user: dict = Depends(get_current_user)):
    stats = security_service.dashboard_stats()
    stats["recent_access"] = _recent_access(20)
    stats["recent_movements"] = security_service.list_movements(20)
    stats["security_events"] = security_service.security_events(50)
    stats["analyses"] = security_service.list_video_analyses(20)
    return stats


def _recent_access(limit):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM access_records ORDER BY recorded_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------- access records
@router.get("/security/access-records")
def list_access_records(direction: str = None, status: str = None, limit: int = Query(100, ge=1, le=500), user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    query = "SELECT * FROM access_records"
    clauses = []
    params = []
    if direction:
        clauses.append("direction = ?")
        params.append(direction.upper())
    if status:
        clauses.append("verification_status = ?")
        params.append(status.upper())
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY recorded_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.post("/security/access-records")
def create_access_record(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    direction = (payload.get("direction") or "").upper()
    if direction not in {ENTRY, EXIT}:
        raise HTTPException(status_code=422, detail="direction must be ENTRY or EXIT")
    result = security_service.record_access(
        direction=direction,
        camera_id=payload.get("camera_id"),
        track_id=payload.get("track_id"),
        person_id=payload.get("person_id"),
        person_name=payload.get("person_name"),
        person_type=payload.get("person_type", "EMPLOYEE"),
        card_id=payload.get("card_id"),
        gate_id=payload.get("gate_id"),
        confidence=float(payload.get("confidence", 0.0)),
        metadata=payload.get("metadata"),
    )
    audit(user=user, action="security.access.create", resource_type="access_record", resource_id=result["id"], details={"direction": direction})
    return result


@router.put("/security/access-records/{record_id}/review")
def review_access_record(record_id: str, payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    result = security_service.review_access(
        record_id,
        payload.get("decision", "VERIFIED"),
        user.get("username", "reviewer"),
        payload.get("notes", ""),
    )
    if result.get("status") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="Access record not found")
    if result.get("status") == "ERROR":
        raise HTTPException(status_code=422, detail=result.get("message"))
    audit(user=user, action="security.access.review", resource_type="access_record", resource_id=record_id, details={"decision": payload.get("decision")})
    return result


# ----------------------------------------------------------------------- assets
@router.get("/security/assets")
def list_assets(status: str = None, user: dict = Depends(get_current_user)):
    return security_service.list_assets(status)


@router.post("/security/assets")
def create_asset(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    result = security_service.create_asset(payload)
    audit(user=user, action="security.asset.create", resource_type="asset", resource_id=result["id"])
    return result


@router.put("/security/assets/{asset_id}")
def update_asset(asset_id: str, payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    result = security_service.update_asset(asset_id, payload)
    if result.get("status") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="Asset not found")
    audit(user=user, action="security.asset.update", resource_type="asset", resource_id=asset_id)
    return result


@router.delete("/security/assets/{asset_id}")
def delete_asset(asset_id: str, user: dict = Depends(require_roles("admin", "operator"))):
    result = security_service.delete_asset(asset_id)
    audit(user=user, action="security.asset.delete", resource_type="asset", resource_id=asset_id)
    return result


@router.get("/security/assets/verify")
def verify_asset(rfid: str = None, barcode: str = None, asset_id: str = None, user: dict = Depends(get_current_user)):
    if not (rfid or barcode or asset_id):
        raise HTTPException(status_code=422, detail="rfid, barcode or asset_id is required")
    return security_service.verify_asset(rfid_tag=rfid, barcode=barcode, asset_id=asset_id)


@router.post("/security/assets/{asset_id}/movements")
def record_asset_movement(asset_id: str, payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    direction = (payload.get("direction") or "").upper()
    if direction not in {ENTRY, EXIT}:
        raise HTTPException(status_code=422, detail="direction must be ENTRY or EXIT")
    result = security_service.record_asset_movement(
        asset_id=asset_id,
        direction=direction,
        gate_id=payload.get("gate_id"),
        camera_id=payload.get("camera_id"),
        verification_source=payload.get("verification_source", "RFID"),
        metadata=payload.get("metadata"),
    )
    audit(user=user, action="security.asset.movement", resource_type="asset", resource_id=asset_id, details={"direction": direction})
    return result


@router.get("/security/assets/movements")
def list_asset_movements(limit: int = Query(100, ge=1, le=500), user: dict = Depends(get_current_user)):
    return security_service.list_movements(limit)


# ------------------------------------------------------------------- gate passes
@router.get("/security/gate-passes")
def list_gate_passes(limit: int = Query(100, ge=1, le=500), user: dict = Depends(get_current_user)):
    return security_service.list_gate_passes(limit)


@router.post("/security/gate-passes")
def create_gate_pass(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    result = security_service.create_gate_pass(payload)
    audit(user=user, action="security.gate_pass.create", resource_type="gate_pass", resource_id=result["id"])
    return result


# --------------------------------------------------------------------- thermal
@router.get("/security/thermal/readings")
def list_thermal_readings(limit: int = Query(100, ge=1, le=500), user: dict = Depends(get_current_user)):
    return security_service.list_thermal_readings(limit)


@router.post("/security/thermal/readings")
def create_thermal_reading(payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    temperature = payload.get("temperature_c")
    if temperature is not None and not payload.get("calibrated", False):
        raise HTTPException(
            status_code=422,
            detail="Temperature readings require a calibrated thermal source (calibrated=true).",
        )
    result = security_service.record_thermal_reading(
        camera_id=payload.get("camera_id"),
        temperature_c=temperature,
        track_id=payload.get("track_id"),
        person_id=payload.get("person_id"),
        threshold_c=payload.get("threshold_c"),
        bbox=payload.get("bbox"),
        calibrated=bool(payload.get("calibrated", False)),
    )
    audit(user=user, action="security.thermal.reading", resource_type="thermal_reading", resource_id=result["id"])
    return result


# --------------------------------------------------------- recorded video analysis
@router.post("/security/analyze-video")
async def analyze_video(
    file: UploadFile = File(...),
    source_kind: str = Form("cctv"),
    model_id: str = Form(None),
    conf: float = Form(0.25),
    sample_fps: float = Form(0.0),
    max_frames: int = Form(300),
    camera_id: str = Form(None),
    gate_id: str = Form(None),
    user: dict = Depends(require_roles("admin", "operator")),
):
    source_kind = (source_kind or "cctv").lower()
    if source_kind not in {"cctv", "thermal"}:
        raise HTTPException(status_code=422, detail="source_kind must be 'cctv' or 'thermal'")
    if source_kind == "thermal":
        model_id = model_id or THERMAL_MODEL_ID
    else:
        model_id = model_id or CCTV_MODEL_ID

    content = await file.read()
    _decode_upload(content, Path(file.filename or "video.mp4").suffix or ".mp4", MAX_VIDEO_BYTES)
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    if suffix.lower() not in VIDEO_SUFFIXES:
        raise HTTPException(status_code=422, detail="Unsupported video format")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
        temporary.write(content)
        temporary_path = temporary.name
    try:
        frames = _sample_frames(temporary_path, sample_fps, max_frames)
        if not frames:
            raise HTTPException(status_code=422, detail="No frames could be decoded from the uploaded video")
        result = security_service.analyze_recorded_video(
            filename=file.filename or "recorded.mp4",
            frames=frames,
            source_kind=source_kind,
            model_id=model_id,
            gate_id=gate_id,
            confidence=conf,
            camera_id=camera_id,
        )
        audit(
            user=user,
            action="security.video.analyze",
            resource_type="video_analysis",
            resource_id=result["analysis_id"],
            details={"source_kind": source_kind, "frames": result["frames_processed"]},
        )
        return result
    finally:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass


@router.get("/security/video-analyses")
def list_video_analyses(limit: int = Query(100, ge=1, le=500), user: dict = Depends(get_current_user)):
    return security_service.list_video_analyses(limit)


# ---------------------------------------------------------------- hardware status
@router.get("/security/hardware/status")
def hardware_status(user: dict = Depends(get_current_user)):
    """Report every hardware category with an honest connection state.

    Cameras are handled by the camera manager; the remaining categories are
    represented by modular adapters that report NOT_CONNECTED until a real
    device/endpoint is configured.
    """
    device_types = [
        "thermal_camera",
        "cctv_camera",
        "rfid_reader",
        "access_control",
        "turnstile",
        "alarm",
        "central_server",
        "storage_backup",
    ]
    results = []
    for device_type in device_types:
        adapter = create_hardware_adapter(device_type, {})
        status = adapter.status() if adapter else {"kind": device_type, "connected": False}
        results.append(status)

    # Camera layer status comes from the real camera manager, not from adapters.
    from camera_worker import camera_worker_manager

    conn = get_db_connection()
    camera_rows = conn.execute("SELECT status, source_type FROM cameras").fetchall()
    conn.close()
    connected = sum(1 for row in camera_rows if (row["status"] or "").upper() == "ONLINE")
    return {
        "devices": results,
        "cameras": {
            "registered": len(camera_rows),
            "connected": connected,
            "workers_running": len([w for w in camera_worker_manager.status() if w.get("running")]),
        },
        "note": "All non-camera devices report NOT_CONNECTED until real hardware is configured.",
    }


@router.post("/security/hardware/{device_type}/connect")
def connect_hardware(device_type: str, payload: dict = Body(...), user: dict = Depends(require_roles("admin", "operator"))):
    adapter = create_hardware_adapter(device_type, payload.get("config", {}))
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"Unknown hardware device type: {device_type}")
    result = adapter.connect()
    audit(user=user, action="security.hardware.connect", resource_type="hardware", resource_id=device_type, details=result)
    return result


# ------------------------------------------------------------------ system events
@router.get("/security/events")
def security_events(limit: int = Query(100, ge=1, le=500), user: dict = Depends(get_current_user)):
    return security_service.security_events(limit)


# ------------------------------------------------------------------ thermal AI
@router.get("/security/thermal/status")
def thermal_status(user: dict = Depends(get_current_user)):
    """Report thermal device + model + calibration state honestly.

    Temperature data is only reported as usable when a genuine radiometric
    source AND a complete calibration record are present.
    """
    from camera_worker import camera_worker_manager
    from hardware_adapters import create_hardware_adapter

    adapter = create_hardware_adapter("thermal_camera", {})
    device = adapter.status() if adapter else {"kind": "thermal_camera", "connected": False}
    model = inference_service.model_status("model-thermal-person")

    conn = get_db_connection()
    calibrated_readings = conn.execute(
        "SELECT COUNT(*) FROM thermal_readings WHERE calibrated = 1"
    ).fetchone()[0]
    latest = conn.execute(
        "SELECT * FROM thermal_readings ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    return {
        "device": device,
        "model": {
            "model_id": "model-thermal-person",
            "status": model.get("status"),
            "loaded": bool(model.get("loaded")),
            "available": model.get("status") == "AVAILABLE",
        },
        "calibration": {
            "device_calibrated": bool(device.get("calibrated")),
            "calibrated_readings": calibrated_readings,
        },
        "latest_reading": dict(latest) if latest else None,
        "workers": [w for w in camera_worker_manager.status() if "thermal" in (w.get("model_ids") or [])],
        "note": (
            "Thermal inference is unavailable until a genuine thermal model is trained. "
            "Temperatures are shown only for calibrated radiometric sources and are not a medical diagnosis."
        ),
    }


@router.post("/security/thermal/ingest")
async def thermal_ingest(
    file: UploadFile = File(...),
    camera_id: str = Form(...),
    threshold_c: float = Form(None),
    user: dict = Depends(require_roles("admin", "operator")),
):
    """Ingest a genuine radiometric thermal export and store a calibrated reading.

    Rejects RGB images and uncalibrated/malformed radiometric data with a clear
    reason. Temperatures are never fabricated.
    """
    from thermal_ingestion import ThermalDataError, inspect_thermal_file, record_thermal_reading

    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded thermal file is empty")
    suffix = Path(file.filename or "thermal.csv").suffix or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
        temporary.write(content)
        temporary_path = temporary.name
    try:
        report = inspect_thermal_file(temporary_path)
        if not report["temperature_usable"]:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Thermal data rejected: "
                    + (report["calibration"].get("reason") or "readings outside plausible range")
                ),
            )
        result = record_thermal_reading(
            temporary_path,
            camera_id,
            threshold_c=threshold_c,
        )
        audit(
            user=user,
            action="thermal.ingest",
            resource_type="thermal_reading",
            resource_id=result["id"],
            details={"camera_id": camera_id, "temperature_c": result["temperature_c"]},
        )
        return {"status": "RECORDED", **result, "inspection": report}
    except ThermalDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass


# --------------------------------------------------------------- inference workers
@router.get("/security/workers")
def inference_workers(user: dict = Depends(get_current_user)):
    """Report the real state of background camera inference workers.

    Shows which cameras are genuinely being analyzed and any errors. Never
    fabricates a running worker for a camera that is not connected.
    """
    from camera_worker import camera_worker_manager

    return {"workers": camera_worker_manager.status()}