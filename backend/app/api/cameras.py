"""Camera management, read-only telemetry, and explicit health refresh.

Reading camera health never evaluates, writes, or creates an event. Evaluation
is an explicit operator action (``POST /cameras/{id}/health/evaluate``) or the
background health worker's job, so an ordinary page view cannot generate safety
records.

Laptop webcams are registered through the same camera model as any other source,
using a ``webcam://`` URL. A webcam changes nothing about the pipeline: the same
reader, frame buffer, health monitor and inference pipeline consume its frames.
"""
from typing import Optional, List

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, require_permission, require_role
    from app.database import get_db
    from app.models import User
    from app.schemas_camera import (
        CameraCreate,
        CameraHealthOut,
        CameraHealthSnapshotOut,
        CameraOut,
        CameraUpdate,
    )
    from app.services.camera_manager import CameraManager
    from app.services.health_monitor import (
        health_monitor,
        read_camera_health,
        stale_health,
    )
    from app.services.inference_pipeline import pipeline_manager
    from app.services.video_ingestion import ingestion_manager
    from app.services.webcam_source import (
        MAX_PROBE_INDEX,
        discover_devices,
    )
except ImportError:
    from backend.app.access_control import log_audit_event, require_permission, require_role
    from backend.app.database import get_db
    from backend.app.models import User
    from backend.app.schemas_camera import (
        CameraCreate,
        CameraHealthOut,
        CameraHealthSnapshotOut,
        CameraOut,
        CameraUpdate,
    )
    from backend.app.services.camera_manager import CameraManager
    from backend.app.services.health_monitor import (
        health_monitor,
        read_camera_health,
        stale_health,
    )
    from backend.app.services.inference_pipeline import pipeline_manager
    from backend.app.services.video_ingestion import ingestion_manager
    from backend.app.services.webcam_source import (
        MAX_PROBE_INDEX,
        discover_devices,
    )


router = APIRouter()


def client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def require_camera(db: Session, camera_id: str):
    camera = CameraManager.get_camera(db, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def register_camera(
    camera_in: CameraCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    """Register a camera and begin ingesting its configured source.

    Credentials in the stream URL are encrypted at rest and never echoed back.
    """
    try:
        camera = CameraManager.create_camera(db, camera_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pipeline_manager.start_stream(camera.id, camera.stream_url, camera.fps, operator_start=True)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="CAMERA_CREATED",
        resource_type="CAMERA",
        resource_id=camera.id,
        details_json={
            "camera_code": camera.code,
            "changed_fields": [
                "name", "code", "stream_url", "camera_type", "fps",
                "resolution", "location_description", "zone_id",
            ],
        },
        ip_address=client_ip(request),
    )
    return camera


@router.get("", response_model=List[CameraOut])
def list_cameras(
    zone_id: Optional[str] = None,
    active_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("cameras:view")),
):
    """List cameras. The returned stream_url is always sanitized."""
    return CameraManager.list_cameras(
        db, zone_id=zone_id, active_only=active_only, limit=limit, offset=offset
    )


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(
    camera_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("cameras:view")),
):
    """Return camera configuration and its last recorded health state."""
    return require_camera(db, camera_id)


@router.put("/{camera_id}", response_model=CameraOut)
def update_camera(
    camera_id: str,
    camera_in: CameraUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN")),
):
    """Update camera configuration or stream URL (ADMIN only)."""
    changed_fields = sorted(camera_in.model_dump(exclude_unset=True))
    try:
        camera = CameraManager.update_camera(db, camera_id, camera_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    if camera.is_active:
        pipeline_manager.start_stream(camera.id, camera.stream_url, camera.fps, operator_start=True)
    else:
        pipeline_manager.stop_stream(camera.id)

    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="CAMERA_UPDATED",
        resource_type="CAMERA",
        resource_id=camera.id,
        details_json={"camera_code": camera.code, "changed_fields": changed_fields},
        ip_address=client_ip(request),
    )
    return camera


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_camera(
    camera_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN")),
):
    """Deactivate a camera and stop its ingestion worker (ADMIN only)."""
    camera = require_camera(db, camera_id)
    CameraManager.delete_camera(db, camera_id)
    pipeline_manager.stop_stream(camera_id, operator_initiated=True)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="CAMERA_DEACTIVATED",
        resource_type="CAMERA",
        resource_id=camera_id,
        details_json={"camera_code": camera.code},
        ip_address=client_ip(request),
    )


@router.get("/{camera_id}/health", response_model=CameraHealthSnapshotOut)
def get_camera_health_snapshot(
    camera_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("cameras:view")),
):
    """Return the last recorded health observation.

    Read-only. This endpoint never evaluates the source, never writes a row, and
    never creates a ``CAMERA_FAILURE`` event.
    """
    camera = require_camera(db, camera_id)
    snapshot = read_camera_health(db, camera)
    snapshot["is_active"] = camera.is_active
    snapshot["observation_stale"] = stale_health(snapshot)
    reader = ingestion_manager.get_reader(camera_id)
    snapshot["reader_status"] = reader.status if reader else "NOT_STARTED"
    snapshot["pipeline_states"] = pipeline_manager.pipeline_status(camera_id)
    return snapshot


@router.post("/{camera_id}/health/evaluate", response_model=CameraHealthOut)
def evaluate_camera_health(
    camera_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("cameras:view_live")),
):
    """Observe the camera now and persist the result.

    This is the explicit evaluation path. It can write the health row and can
    record a ``CAMERA_FAILURE`` event when the observed source is unavailable,
    so it is audited.
    """
    camera = require_camera(db, camera_id)
    health = health_monitor.evaluate_camera_health(db, camera)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="CAMERA_HEALTH_EVALUATED",
        resource_type="CAMERA",
        resource_id=camera.id,
        details_json={"observed_status": health.status},
        ip_address=client_ip(request),
    )
    return health


# ===========================================================================
# LAPTOP WEBCAM
# ===========================================================================


class WebcamDeviceOut(BaseModel):
    index: int
    available: bool
    name: Optional[str] = None
    opened: bool = False
    read_frame: bool = False
    resolution: Optional[List[int]] = None
    reported_fps: Optional[float] = None
    error: Optional[str] = None
    backend: Optional[str] = None


class WebcamDeviceListOut(BaseModel):
    """Result of probing device indices.

    ``probe_limit`` records how far probing went. A device is only reported
    available when a frame was actually read back from it; manufacturer and model
    names are never invented.
    """

    devices: List[WebcamDeviceOut]
    probe_limit: int
    available_count: int
    discovery_state: str


class WebcamRegisterRequest(BaseModel):
    """Register the laptop webcam as a normal camera in the existing model."""

    device_index: int = Field(default=0, ge=0, le=MAX_PROBE_INDEX)
    width: int = Field(default=1280, ge=160, le=4096)
    height: int = Field(default=720, ge=120, le=2160)
    fps: float = Field(default=15.0, ge=1.0, le=120.0)
    name: str = Field(default="Laptop Webcam", min_length=2, max_length=150)
    code: Optional[str] = Field(default=None, max_length=50)
    zone_id: Optional[str] = Field(default=None, max_length=36)
    location_description: Optional[str] = Field(default=None, max_length=200)


class WebcamStatusOut(BaseModel):
    """Observed source state for a running camera.

    Every rate and count is measured from frames that actually arrived. A camera
    that has not produced a frame reports ``measured_fps`` as null.
    """

    camera_id: str
    source_type: str
    stream_state: str
    running: bool
    is_connected: bool
    device_index: Optional[int] = None
    source_backend: Optional[str] = None
    observed_frames: bool
    total_frames_read: int
    measured_fps: Optional[float] = None
    dropped_frames: int
    decode_failures: int
    reconnects: int
    last_frame_timestamp: Optional[str] = None
    last_frame_interval_ms: Optional[float] = None
    resolution: Optional[List[int]] = None
    source_reported_fps: Optional[float] = None
    target_fps: Optional[float] = None
    inference_state: str
    detections_observed: int
    last_error: Optional[str] = None
    model_state: str
    health: dict


@router.get("/webcam/devices", response_model=WebcamDeviceListOut)
def discover_webcam_devices(
    max_index: int = Query(default=MAX_PROBE_INDEX, ge=0, le=MAX_PROBE_INDEX),
    read_frame: bool = Query(default=True),
    actor: Optional[User] = Depends(require_permission("cameras:view")),
):
    """Probe webcam device indices and report which ones can really be opened.

    Windows and OpenCV cannot reliably enumerate webcam names, so each index in a
    bounded range is opened and a frame is read back. A device is reported
    available only if that frame really arrived.
    """
    probes = discover_devices(max_index=max_index, read_frame=read_frame)
    available = [probe for probe in probes if probe.available]
    return {
        "devices": [probe.as_dict() for probe in probes],
        "probe_limit": max_index,
        "available_count": len(available),
        "discovery_state": "AVAILABLE" if available else "NO_WEBCAM_DETECTED",
    }


@router.post("/webcam/register", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def register_webcam_camera(
    payload: WebcamRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    """Register the laptop webcam through the existing camera model.

    The webcam is only registered if a frame was actually read from that device,
    so the database never gains a camera that does not exist.
    """
    from app.services.webcam_source import probe_device

    probe = probe_device(payload.device_index, read_frame=True)
    if not probe.available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No frame could be read from webcam device index {payload.device_index}. "
                f"Reason: {probe.error or 'device could not be opened'}"
            ),
        )

    stream_url = f"webcam://{payload.device_index}"

    camera_in = CameraCreate(
        name=payload.name,
        code=payload.code or f"WEBCAM-{payload.device_index}",
        stream_url=stream_url,
        camera_type="WEBCAM",
        fps=payload.fps,
        resolution=f"{payload.width}x{payload.height}",
        zone_id=payload.zone_id,
        location_description=payload.location_description,
    )
    try:
        camera = CameraManager.create_camera(db, camera_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pipeline_manager.start_stream(camera.id, camera.stream_url, camera.fps, operator_start=True)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="WEBCAM_CAMERA_REGISTERED",
        resource_type="CAMERA",
        resource_id=camera.id,
        details_json={
            "device_index": payload.device_index,
            "observed_resolution": list(probe.resolution) if probe.resolution else None,
            "capture_backend": probe.backend,
        },
        ip_address=client_ip(request),
    )
    return camera


@router.post("/{camera_id}/start", response_model=WebcamStatusOut)
def start_camera_source(
    camera_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    """Start (or restart) a camera's configured source.

    Starting a camera never creates a safety event and never reports a health
    verdict: the health row is only written once a frame has been observed.
    """
    camera = require_camera(db, camera_id)
    if not camera.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Camera is deactivated; activate it before starting its source",
        )
    pipeline_manager.start_stream(camera.id, camera.stream_url, camera.fps)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="CAMERA_SOURCE_STARTED",
        resource_type="CAMERA",
        resource_id=camera.id,
        details_json={"camera_code": camera.code},
        ip_address=client_ip(request),
    )
    return _webcam_status(db, camera)


@router.post("/{camera_id}/stop", response_model=WebcamStatusOut)
def stop_camera_source(
    camera_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    """Stop a camera's ingestion worker. Stopping is idempotent."""
    camera = require_camera(db, camera_id)
    pipeline_manager.stop_stream(camera_id)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="CAMERA_SOURCE_STOPPED",
        resource_type="CAMERA",
        resource_id=camera.id,
        details_json={"camera_code": camera.code},
        ip_address=client_ip(request),
    )
    return _webcam_status(db, camera)


@router.get("/{camera_id}/status", response_model=WebcamStatusOut)
def get_camera_source_status(
    camera_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("cameras:view")),
):
    """Return the observed source state, including measured rate and counts."""
    camera = require_camera(db, camera_id)
    return _webcam_status(db, camera)


def _webcam_status(db: Session, camera) -> WebcamStatusOut:
    reader = ingestion_manager.get_reader(camera.id)
    telemetry = reader.telemetry() if reader is not None else {
        "source_type": "UNKNOWN",
        "status": "NOT_STARTED",
        "stream_state": "NOT_STARTED",
        "is_connected": False,
        "total_frames_read": 0,
        "dropped_frames": 0,
        "decode_failures": 0,
        "reconnects": 0,
        "observed_frames": False,
    }
    pipelines = pipeline_manager.pipeline_status(camera.id)
    pipeline = pipelines[0] if pipelines else None
    snapshot = read_camera_health(db, camera)
    snapshot["observation_stale"] = stale_health(snapshot)
    snapshot["reader_status"] = reader.status if reader else "NOT_STARTED"

    return WebcamStatusOut(
        camera_id=camera.id,
        source_type=telemetry.get("source_type", "UNKNOWN"),
        stream_state=telemetry.get("stream_state", "UNKNOWN"),
        running=bool(telemetry.get("is_connected")) and bool(telemetry.get("observed_frames")),
        is_connected=bool(telemetry.get("is_connected")),
        device_index=telemetry.get("device_index"),
        source_backend=telemetry.get("source_backend"),
        observed_frames=bool(telemetry.get("observed_frames")),
        total_frames_read=int(telemetry.get("total_frames_read") or 0),
        measured_fps=telemetry.get("current_fps"),
        dropped_frames=int(telemetry.get("dropped_frames") or 0),
        decode_failures=int(telemetry.get("decode_failures") or 0),
        reconnects=int(telemetry.get("reconnects") or 0),
        last_frame_timestamp=(
            telemetry["last_frame_timestamp"].isoformat() if telemetry.get("last_frame_timestamp") else None
        ),
        last_frame_interval_ms=telemetry.get("last_frame_interval_ms"),
        resolution=telemetry.get("source_resolution"),
        source_reported_fps=telemetry.get("source_fps"),
        target_fps=telemetry.get("target_fps"),
        # Inference is reported from what the pipeline actually did. With no
        # weights configured this stays NOT_RUNNING and the detection count stays
        # zero, because no detector has run.
        inference_state=(pipeline or {}).get("pipeline_state", "NOT_RUNNING"),
        detections_observed=int((pipeline or {}).get("detection_count") or 0),
        last_error=telemetry.get("last_error"),
        model_state=pipeline_manager.model_health().get("status", "UNKNOWN"),
        health=snapshot,
    )


@router.get("/{camera_id}/preview.mjpg")
def stream_camera_preview(
    camera_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("cameras:view_live")),
):
    """Stream real captured frames as multipart JPEG.

    Frames come from the same buffer the pipeline consumes, so the preview shows
    what was actually received. Nothing is synthesised: if no frame has arrived,
    the stream simply carries nothing and the client shows no image.
    """
    import cv2

    camera = require_camera(db, camera_id)
    reader = ingestion_manager.get_reader(camera_id)
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Camera source is not started; start it before requesting a preview",
        )

    def frame_stream():
        boundary = "frame"
        while True:
            latest = reader.get_latest_frame()
            if latest is not None:
                _timestamp, frame = latest
                success, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                if success:
                    payload = encoded.tobytes()
                    yield (
                        f"--{boundary}\r\n"
                        "Content-Type: image/jpeg\r\n"
                        f"Content-Length: {len(payload)}\r\n\r\n"
                    ).encode() + payload + b"\r\n"
            else:
                # No frame observed yet: emit a tiny comment line instead of an
                # image, so the client never receives a fabricated picture.
                yield f"--{boundary}\r\nX-Frame-State: NO_FRAME_OBSERVED\r\n\r\n".encode()
            time.sleep(1.0 / max(reader.target_fps, 1.0))

    return StreamingResponse(
        frame_stream(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary_value()}",
        headers={"Cache-Control": "no-store", "X-Frame-Source": "REAL_CAPTURED_FRAMES"},
    )


def boundary_value() -> str:
    return "frame"


@router.get("/{camera_id}/snapshot.jpg")
def get_camera_snapshot(
    camera_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("cameras:view_live")),
):
    """Return the most recent real captured frame as a JPEG image.

    Returns 404 with an explicit state when no frame has been observed, rather
    than substituting a placeholder image.
    """
    import cv2

    camera = require_camera(db, camera_id)
    reader = ingestion_manager.get_reader(camera_id)
    latest = reader.get_latest_frame() if reader is not None else None
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="EVIDENCE_NOT_AVAILABLE: no frame has been observed from this camera yet",
        )
    _timestamp, frame = latest
    success, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not success:
        raise HTTPException(status_code=500, detail="Latest frame could not be encoded")
    return Response(content=encoded.tobytes(), media_type="image/jpeg")
