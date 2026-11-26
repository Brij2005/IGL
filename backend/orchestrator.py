"""Central orchestration layer for the IGL VisionGuard centralized AI architecture.

This module provides the read/aggregation and control operations that back the
operator-facing pages:

    * AI Model Hub            — every registered model, its status, loading
                                state, versions and model-to-camera usage.
    * Camera Command Centre   — every camera with live status, current scene,
                                active models, active detections and health.
    * Model-to-Camera Mapping — which models are (or would be) running on each
                                camera, and why.
    * Mode control            — switch a camera between AUTOMATIC and MANUAL,
                                set manual model assignments, reset decisions.

All values are derived from real persisted state plus live worker/scene data.
Nothing is fabricated and no device is reported connected unless it genuinely is.
"""

from __future__ import annotations

import json

from database import get_db_connection
from model_registry import capability_for, describe_registry

# Event types that are considered "genuine detections" for the active-detection
# panel. These map to the event engine's normalized event_type values.
_ACTIVE_EVENT_STATUSES = ("DETECTED", "NEW")


def _json_loads(value, default):
    try:
        return json.loads(value or "{}")
    except Exception:
        return default


def _camera_config(camera_row) -> dict:
    return _json_loads(camera_row["config_json"] if "config_json" in camera_row.keys() else "{}", {})


# --------------------------------------------------------------------- Model Hub
def model_hub() -> dict:
    """Return the full centralized AI Model Hub payload.

    For every model registered in ``ai_models`` we report:
        * identity + purpose + supported classes
        * lifecycle status (AVAILABLE / NOT_TRAINED / NOT_DEPLOYED)
        * whether it is currently loaded into the inference service
        * version history
        * which cameras reference it (model-to-camera usage)
    """
    from inference_service import inference_service

    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM ai_models ORDER BY name").fetchall()
    version_rows = conn.execute(
        "SELECT model_id, version, weights_path, status, dataset_id, created_at FROM model_versions ORDER BY model_id, created_at"
    ).fetchall()
    camera_rows = conn.execute("SELECT id, name, source_type, config_json, status FROM cameras").fetchall()
    conn.close()

    versions_by_model = {}
    for row in version_rows:
        versions_by_model.setdefault(row["model_id"], []).append(dict(row))

    # Which cameras reference each model (config_json.model_ids / manual mode).
    usage = {}
    for cam in camera_rows:
        config = _json_loads(cam["config_json"], {})
        model_ids = config.get("model_ids") or []
        if isinstance(model_ids, str):
            model_ids = [model_ids]
        for model_id in model_ids:
            usage.setdefault(model_id, []).append({"camera_id": cam["id"], "camera_name": cam["name"]})

    models = []
    for row in rows:
        model_id = row["id"]
        capability = capability_for(model_id)
        loaded = inference_service.get_model(model_id) is not None
        models.append(
            {
                "model_id": model_id,
                "name": row["name"],
                "display_name": capability.get("display_name") or row["name"],
                "purpose": capability.get("purpose"),
                "task_type": row["task_type"],
                "architecture": row["architecture"],
                "status": row["status"],
                "loaded": bool(loaded),
                "available": row["status"] == "AVAILABLE",
                "active_version": row["active_version"],
                "weights_path": row["weights_path"],
                "inputs": capability.get("inputs", []),
                "scenes": capability.get("scenes", []),
                "classes": _json_loads(row["classes_json"], {}),
                "versions": versions_by_model.get(model_id, []),
                "used_by_cameras": usage.get(model_id, []),
                "metadata": _json_loads(row["metadata_json"], {}),
            }
        )

    return {
        "models": models,
        "registry": describe_registry(),
        "summary": {
            "total": len(models),
            "available": len([m for m in models if m["available"]]),
            "untrained": len([m for m in models if m["status"] == "NOT_TRAINED"]),
            "unavailable": len([m for m in models if not m["available"]]),
            "loaded": len([m for m in models if m["loaded"]]),
        },
    }


# -------------------------------------------------------------- Camera Command
def camera_command_center() -> dict:
    """Return the centralized Camera Command Centre payload.

    Merges the persisted camera registry with live worker state (scene, active
    models, frame freshness, last error) and recent detections per camera.
    """
    from camera_worker import camera_worker_manager

    conn = get_db_connection()
    camera_rows = conn.execute("SELECT * FROM cameras ORDER BY name").fetchall()
    # Recent detection events per camera (last 5 each).
    event_rows = conn.execute(
        """
        SELECT id, camera_id, model_id, event_type, severity, status, confidence,
               evidence_id, created_at
        FROM detection_events
        ORDER BY created_at DESC
        LIMIT 500
        """
    ).fetchall()
    # Active alert counts per camera.
    alert_rows = conn.execute(
        "SELECT camera_id, COUNT(*) AS c FROM alerts WHERE status IN ('DETECTED','ACKNOWLEDGED','ESCALATED') GROUP BY camera_id"
    ).fetchall()
    conn.close()

    events_by_camera = {}
    for row in event_rows:
        events_by_camera.setdefault(row["camera_id"], []).append(dict(row))

    alert_counts = {row["camera_id"]: row["c"] for row in alert_rows}
    worker_by_camera = {w["camera_id"]: w for w in camera_worker_manager.status()}

    cameras = []
    for row in camera_rows:
        camera = dict(row)
        camera.pop("credentials_json", None)
        camera["has_credentials"] = bool(row["credentials_json"] and row["credentials_json"] not in {"{}", "null"})
        camera["config"] = _json_loads(row["config_json"], {})
        worker = worker_by_camera.get(row["id"], {})
        recent = events_by_camera.get(row["id"], [])[:5]
        cameras.append(
            {
                "camera_id": row["id"],
                "name": row["name"],
                "source_type": row["source_type"],
                "status": row["status"],
                "enabled": bool(row["enabled"]) if "enabled" in row.keys() else True,
                "plant_location_id": row["plant_location_id"] if "plant_location_id" in row.keys() else None,
                "pipeline_id": row["pipeline_id"] if "pipeline_id" in row.keys() else None,
                "mode": camera.get("config", {}).get("mode", "AUTOMATIC"),
                "assigned_model_ids": camera.get("config", {}).get("model_ids", []),
                # Live worker / scene state:
                "worker_running": bool(worker.get("running")),
                "scene": worker.get("scene", "GENERAL"),
                "scene_confidence": worker.get("scene_confidence", 0.0),
                "scene_reason": worker.get("scene_reason", ""),
                "active_model_ids": worker.get("active_model_ids") or worker.get("model_ids", []),
                "frames_processed": worker.get("frames_processed", 0),
                "scene_switch_count": worker.get("scene_switch_count", 0),
                "last_frame_at": worker.get("last_frame_at"),
                "last_error": worker.get("last_error"),
                "last_seen": row["last_seen"] if "last_seen" in row.keys() else None,
                "active_detections": [e for e in recent if e["status"] in _ACTIVE_EVENT_STATUSES],
                "recent_events": recent,
                "active_alert_count": alert_counts.get(row["id"], 0),
            }
        )

    online = [c for c in cameras if (c["status"] or "").upper() == "ONLINE"]
    return {
        "cameras": cameras,
        "summary": {
            "registered": len(cameras),
            "online": len(online),
            "offline": len(cameras) - len(online),
            "workers_running": len([c for c in cameras if c["worker_running"]]),
            "automatic": len([c for c in cameras if c["mode"] == "AUTOMATIC"]),
            "manual": len([c for c in cameras if c["mode"] == "MANUAL"]),
            "with_active_detections": len([c for c in cameras if c["active_detections"]]),
        },
        "note": "Values reflect actual persisted and live worker state; no fabricated metrics.",
    }


# ------------------------------------------------------------ Mapping / modes
def model_camera_mapping() -> dict:
    """Return the model-to-camera mapping with selection reasoning.

    For each camera, shows the models it is assigned/using (actual worker state
    when running, otherwise the configured intent) and the current mode/scene.
    """
    from camera_worker import camera_worker_manager
    from mode_manager import mode_manager

    conn = get_db_connection()
    camera_rows = conn.execute("SELECT * FROM cameras ORDER BY name").fetchall()
    model_rows = conn.execute("SELECT id, name, status FROM ai_models").fetchall()
    conn.close()

    model_status = {row["id"]: row["status"] for row in model_rows}
    worker_by_camera = {w["camera_id"]: w for w in camera_worker_manager.status()}

    mappings = []
    for row in camera_rows:
        config = _json_loads(row["config_json"], {})
        mode = mode_manager.get_mode(dict(row))
        worker = worker_by_camera.get(row["id"], {})
        decision = mode_manager.last_decision(row["id"])
        if worker.get("running"):
            active_models = worker.get("active_model_ids") or worker.get("model_ids", [])
        else:
            active_models = config.get("model_ids", []) or []
        mapped = []
        for model_id in active_models:
            mapped.append(
                {
                    "model_id": model_id,
                    "model_name": next((m["name"] for m in model_rows if m["id"] == model_id), model_id),
                    "status": model_status.get(model_id, "UNKNOWN"),
                }
            )
        mappings.append(
            {
                "camera_id": row["id"],
                "camera_name": row["name"],
                "source_type": row["source_type"],
                "mode": mode,
                "scene": worker.get("scene", "GENERAL"),
                "worker_running": bool(worker.get("running")),
                "assigned_model_ids": config.get("model_ids", []) or [],
                "active_models": mapped,
                "decision": decision,
            }
        )

    return {
        "mappings": mappings,
        "summary": {
            "cameras": len(mappings),
            "automatic": len([m for m in mappings if m["mode"] == "AUTOMATIC"]),
            "manual": len([m for m in mappings if m["mode"] == "MANUAL"]),
        },
    }


# ------------------------------------------------------------------- mode control
def set_camera_mode(camera_id: str, mode: str) -> dict:
    from mode_manager import mode_manager

    conn = get_db_connection()
    exists = conn.execute("SELECT id FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    conn.close()
    if not exists:
        raise ValueError("Camera not found")
    return mode_manager.set_mode(camera_id, mode)


def set_camera_manual_models(camera_id: str, model_ids: list) -> dict:
    """Pin an explicit manual model list for a camera (Manual mode).

    The list is stored in config_json.model_ids. The mode manager still filters
    out unavailable/untrained/incompatible models at run time.
    """
    conn = get_db_connection()
    row = conn.execute("SELECT config_json FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError("Camera not found")
    try:
        config = json.loads(row["config_json"] or "{}")
    except Exception:
        config = {}
    config["model_ids"] = list(model_ids)
    config["mode"] = "MANUAL"
    from database import now_iso

    conn.execute(
        "UPDATE cameras SET config_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config), now_iso(), camera_id),
    )
    conn.commit()
    conn.close()

    from mode_manager import mode_manager

    mode_manager.reset(camera_id)
    return {"camera_id": camera_id, "mode": "MANUAL", "model_ids": list(model_ids)}


def detections_feed(limit: int = 100, camera_id: str = None, event_type: str = None) -> list:
    """Return a centralized detections feed joining events + evidence + model."""
    conn = get_db_connection()
    query = [
        "SELECT e.*, c.name AS camera_name, m.name AS model_name, f.file_path AS evidence_path",
        "FROM detection_events e",
        "LEFT JOIN cameras c ON c.id = e.camera_id",
        "LEFT JOIN ai_models m ON m.id = e.model_id",
        "LEFT JOIN evidence_files f ON f.id = e.evidence_id",
    ]
    clauses = []
    params = []
    if camera_id:
        clauses.append("e.camera_id = ?")
        params.append(camera_id)
    if event_type:
        clauses.append("e.event_type = ?")
        params.append(event_type)
    if clauses:
        query.append("WHERE " + " AND ".join(clauses))
    query.append("ORDER BY e.created_at DESC LIMIT ?")
    params.append(limit)
    rows = conn.execute("\n".join(query), tuple(params)).fetchall()
    conn.close()
    results = []
    for row in rows:
        item = dict(row)
        item["bbox"] = _json_loads(item.get("bbox_json"), None)
        item["metadata"] = _json_loads(item.get("metadata_json"), {})
        results.append(item)
    return results