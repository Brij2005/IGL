"""Smart Entry & Exit monitoring service for IGL VisionGuard.

Responsibilities:
    * Create entry/exit access records from detections.
    * Verify employees / visitors against access-control records (no face recognition).
    * Verify company assets using the asset register, RFID/barcode and gate passes.
    * Record calibrated thermal temperature readings only when supported.
    * Analyze recorded CCTV / thermal footage and persist results.
    * Compute dashboard statistics strictly from stored records.

Design rules enforced here:
    * AI detections never decide whether an object belongs to the company.
    * Uncertain detections are always sent for human review.
    * Thermal temperature is never reported without a calibrated source.
    * No random/fabricated counts or events.
"""

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from config import EVIDENCE_DIR
from database import get_db_connection, now_iso
from event_engine import EventEngine
from hardware_adapters import create_video_adapter
from inference_service import inference_service

THERMAL_MODEL_ID = "model-thermal-person"
CCTV_MODEL_ID = "model-yolov8n"
PERSON_CLASS = "person"

ENTRY = "ENTRY"
EXIT = "EXIT"

# Gate configuration: each gate declares its zones and which side is entry/exit.
DEFAULT_GATES = [
    {
        "gate_id": "GATE-1",
        "name": "Main Entry Gate",
        "entry_zone": {"x": 0.0, "y": 0.6, "w": 1.0, "h": 0.4},
        "exit_zone": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.4},
    },
    {
        "gate_id": "GATE-2",
        "name": "Dispatch Exit Gate",
        "entry_zone": {"x": 0.0, "y": 0.6, "w": 1.0, "h": 0.4},
        "exit_zone": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.4},
    },
]


def _center(bbox):
    return bbox[0] + bbox[2] / 2.0, bbox[1] + bbox[3] / 2.0


def _point_in_rect(point, rect):
    if not rect:
        return False
    x, y = point
    return rect.get("x", 0.0) <= x <= rect.get("x", 0.0) + rect.get("w", 1.0) and rect.get("y", 0.0) <= y <= rect.get("y", 0.0) + rect.get("h", 1.0)


def _safe_json_loads(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


class _DirectionTracker:
    """Assigns a direction (ENTRY/EXIT/None) to a track based on zone crossings."""

    def __init__(self):
        self._last_zone = {}

    def update(self, track_id, point, gate):
        previous = self._last_zone.get(track_id)
        current = None
        if _point_in_rect(point, gate.get("entry_zone")):
            current = ENTRY
        elif _point_in_rect(point, gate.get("exit_zone")):
            current = EXIT
        direction = None
        if previous and current and previous != current:
            direction = current
        self._last_zone[track_id] = current or previous
        return direction


class SecurityMonitoringService:
    def __init__(self):
        self._tracker = _DirectionTracker()
        self._event_engine = EventEngine()

    # ------------------------------------------------------------------ evidence
    def _save_evidence(self, frame, camera_id=None, source_kind=None):
        """Persist a frame as an evidence image and return its evidence id.

        Reuses the existing `evidence_files` table and `EVIDENCE_DIR` so the
        Evidence Gallery picks it up without any duplicate storage path.
        """
        try:
            ok, buffer = cv2.imencode(".jpg", frame)
            if not ok:
                return None
            image_bytes = buffer.tobytes()
            digest = hashlib.sha256(image_bytes).hexdigest()
            evidence_id = f"EVD-{uuid.uuid4().hex[:10].upper()}"
            path = Path(EVIDENCE_DIR) / f"{evidence_id}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_bytes)
            height, width = frame.shape[:2]
            conn = get_db_connection()
            conn.execute(
                """
                INSERT INTO evidence_files (id, camera_id, file_path, content_type, width, height, sha256, retention_days)
                VALUES (?, ?, ?, 'image/jpeg', ?, ?, ?, 90)
                """,
                (evidence_id, camera_id, str(path), width, height, digest),
            )
            conn.commit()
            conn.close()
            return evidence_id
        except Exception as exc:
            print(f"[SECURITY] Evidence capture failed: {exc}")
            return None

    # ------------------------------------------------------------------ gates
    def _gates(self):
        conn = get_db_connection()
        rows = conn.execute("SELECT value FROM app_settings WHERE key = 'security_gates'").fetchone()
        conn.close()
        if rows:
            parsed = _safe_json_loads(rows["value"], None)
            if parsed:
                return parsed
        return DEFAULT_GATES

    def _gate_for_camera(self, camera_id):
        for gate in self._gates():
            if gate.get("camera_id") == camera_id:
                return gate
        return None

    # ------------------------------------------------------------- access records
    def record_access(
        self,
        direction,
        camera_id,
        track_id=None,
        person_id=None,
        person_name=None,
        person_type="EMPLOYEE",
        card_id=None,
        gate_id=None,
        confidence=0.0,
        evidence_id=None,
        event_id=None,
        metadata=None,
    ):
        record_id = f"ACC-{uuid.uuid4().hex[:10].upper()}"
        metadata = metadata or {}
        verification_status = "VERIFIED" if (person_id or card_id) else "UNVERIFIED"
        verification_source = metadata.get("verification_source")
        if verification_status == "VERIFIED" and not verification_source:
            verification_source = "ACCESS_CONTROL"
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO access_records
            (id, direction, person_id, person_name, person_type, card_id, gate_id, camera_id,
             track_id, verification_source, verification_status, evidence_id, event_id, confidence, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                direction,
                person_id,
                person_name,
                person_type,
                card_id,
                gate_id,
                camera_id,
                track_id,
                verification_source,
                verification_status,
                evidence_id,
                event_id,
                confidence,
                json.dumps(metadata),
            ),
        )
        conn.commit()
        conn.close()
        return {
            "id": record_id,
            "direction": direction,
            "verification_status": verification_status,
            "verification_source": verification_source,
        }

    def review_access(self, record_id, decision, reviewer, notes=""):
        decision = (decision or "").upper()
        if decision not in {"VERIFIED", "UNVERIFIED", "DISMISSED"}:
            return {"status": "ERROR", "message": "decision must be VERIFIED, UNVERIFIED or DISMISSED"}
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM access_records WHERE id = ?", (record_id,)).fetchone()
        if not row:
            conn.close()
            return {"status": "NOT_FOUND"}
        conn.execute(
            "UPDATE access_records SET verification_status = ?, reviewed_by = ?, reviewed_at = ?, review_notes = ?, metadata_json = ? WHERE id = ?",
            (
                decision,
                reviewer,
                now_iso(),
                notes,
                json.dumps({**_safe_json_loads(row["metadata_json"], {}), "human_review": True}),
                record_id,
            ),
        )
        conn.commit()
        conn.close()
        return {"status": "REVIEWED", "id": record_id, "decision": decision}

    # ------------------------------------------------------------ asset register
    def list_assets(self, status=None):
        conn = get_db_connection()
        if status:
            rows = conn.execute("SELECT * FROM asset_register WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM asset_register ORDER BY created_at DESC").fetchall()
        conn.close()
        return [self._asset_dict(dict(row)) for row in rows]

    def _asset_dict(self, row):
        item = dict(row)
        item["metadata"] = _safe_json_loads(item.pop("metadata_json"), {})
        return item

    def create_asset(self, payload):
        asset_id = payload.get("id") or f"AST-{uuid.uuid4().hex[:8].upper()}"
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO asset_register
            (id, asset_name, asset_category, department, rfid_tag, barcode, assigned_employee_id,
             gate_pass_id, expected_return_date, status, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                payload.get("asset_name", "Unnamed Asset"),
                payload.get("asset_category", "GENERAL"),
                payload.get("department", ""),
                payload.get("rfid_tag", ""),
                payload.get("barcode", ""),
                payload.get("assigned_employee_id", ""),
                payload.get("gate_pass_id", ""),
                payload.get("expected_return_date", ""),
                payload.get("status", "IN_PLANT"),
                json.dumps(payload.get("metadata", {})),
            ),
        )
        conn.commit()
        conn.close()
        return {"status": "CREATED", "id": asset_id}

    def update_asset(self, asset_id, payload):
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM asset_register WHERE id = ?", (asset_id,)).fetchone()
        if not row:
            conn.close()
            return {"status": "NOT_FOUND"}
        metadata = {**_safe_json_loads(row["metadata_json"], {}), **payload.get("metadata", {})}
        conn.execute(
            """
            UPDATE asset_register SET asset_name = ?, asset_category = ?, department = ?, rfid_tag = ?,
                barcode = ?, assigned_employee_id = ?, gate_pass_id = ?, expected_return_date = ?,
                status = ?, metadata_json = ?, updated_at = ? WHERE id = ?
            """,
            (
                payload.get("asset_name", row["asset_name"]),
                payload.get("asset_category", row["asset_category"]),
                payload.get("department", row["department"]),
                payload.get("rfid_tag", row["rfid_tag"]),
                payload.get("barcode", row["barcode"]),
                payload.get("assigned_employee_id", row["assigned_employee_id"]),
                payload.get("gate_pass_id", row["gate_pass_id"]),
                payload.get("expected_return_date", row["expected_return_date"]),
                payload.get("status", row["status"]),
                json.dumps(metadata),
                now_iso(),
                asset_id,
            ),
        )
        conn.commit()
        conn.close()
        return {"status": "UPDATED", "id": asset_id}

    def delete_asset(self, asset_id):
        conn = get_db_connection()
        conn.execute("DELETE FROM asset_register WHERE id = ?", (asset_id,))
        conn.commit()
        conn.close()
        return {"status": "DELETED", "id": asset_id}

    def verify_asset(self, rfid_tag=None, barcode=None, asset_id=None):
        """Resolve an asset from the register. AI detection alone never decides ownership."""
        conn = get_db_connection()
        row = None
        if asset_id:
            row = conn.execute("SELECT * FROM asset_register WHERE id = ?", (asset_id,)).fetchone()
        elif rfid_tag:
            row = conn.execute("SELECT * FROM asset_register WHERE rfid_tag = ?", (rfid_tag,)).fetchone()
        elif barcode:
            row = conn.execute("SELECT * FROM asset_register WHERE barcode = ?", (barcode,)).fetchone()
        conn.close()
        if not row:
            return {"verified": False, "reason": "NO_MATCHING_ASSET_RECORD", "asset": None}
        return {"verified": True, "reason": "ASSET_FOUND_IN_REGISTER", "asset": self._asset_dict(dict(row))}

    def list_movements(self, limit=100):
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM asset_movements ORDER BY moved_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def record_asset_movement(self, asset_id, direction, gate_id=None, camera_id=None, verification_source="RFID", evidence_id=None, event_id=None, metadata=None):
        movement_id = f"MOV-{uuid.uuid4().hex[:10].upper()}"
        conn = get_db_connection()
        asset = conn.execute("SELECT * FROM asset_register WHERE id = ?", (asset_id,)).fetchone()
        if not asset:
            conn.close()
            return {"status": "NO_MATCHING_ASSET_RECORD", "id": movement_id}
        gate_pass = None
        if asset["gate_pass_id"]:
            gate_pass = conn.execute("SELECT * FROM gate_passes WHERE id = ?", (asset["gate_pass_id"],)).fetchone()
        verification_status = "MATCHED"
        if not gate_pass or gate_pass["status"] != "OPEN":
            verification_status = "UNMATCHED"
        conn.execute(
            """
            INSERT INTO asset_movements
            (id, asset_id, gate_pass_id, direction, gate_id, camera_id, verification_source,
             verification_status, evidence_id, event_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                movement_id,
                asset_id,
                asset["gate_pass_id"],
                direction,
                gate_id,
                camera_id,
                verification_source,
                verification_status,
                evidence_id,
                event_id,
                json.dumps(metadata or {}),
            ),
        )
        if verification_status == "MATCHED" and direction == EXIT:
            conn.execute("UPDATE asset_register SET status = 'OUT_OF_PLANT', updated_at = ? WHERE id = ?", (now_iso(), asset_id))
        elif verification_status == "MATCHED" and direction == ENTRY:
            conn.execute("UPDATE asset_register SET status = 'IN_PLANT', updated_at = ? WHERE id = ?", (now_iso(), asset_id))
        conn.commit()
        conn.close()
        return {"status": "RECORDED", "id": movement_id, "verification_status": verification_status}

    def list_gate_passes(self, limit=100):
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM gate_passes ORDER BY issued_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def create_gate_pass(self, payload):
        pass_id = payload.get("id") or f"GP-{uuid.uuid4().hex[:8].upper()}"
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO gate_passes (id, asset_id, issued_to_employee_id, purpose, expected_return_date, status, metadata_json)
            VALUES (?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                pass_id,
                payload.get("asset_id", ""),
                payload.get("issued_to_employee_id", ""),
                payload.get("purpose", ""),
                payload.get("expected_return_date", ""),
                json.dumps(payload.get("metadata", {})),
            ),
        )
        conn.commit()
        conn.close()
        return {"status": "CREATED", "id": pass_id}

    # ------------------------------------------------------------------ thermal
    def record_thermal_reading(self, camera_id, temperature_c, track_id=None, person_id=None, threshold_c=None, bbox=None, evidence_id=None, event_id=None, calibrated=False):
        reading_id = f"THM-{uuid.uuid4().hex[:10].upper()}"
        threshold_exceeded = 0
        if calibrated and threshold_c is not None and temperature_c is not None and temperature_c >= float(threshold_c):
            threshold_exceeded = 1
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO thermal_readings
            (id, camera_id, track_id, person_id, temperature_c, threshold_c, threshold_exceeded,
             bbox_json, evidence_id, event_id, calibrated, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading_id,
                camera_id,
                track_id,
                person_id,
                temperature_c,
                threshold_c,
                threshold_exceeded,
                json.dumps(bbox) if bbox else None,
                evidence_id,
                event_id,
                1 if calibrated else 0,
                json.dumps({"note": "Temperature is only recorded when the thermal camera provides calibrated data. Not a medical diagnosis."}),
            ),
        )
        conn.commit()
        conn.close()
        return {"id": reading_id, "threshold_exceeded": bool(threshold_exceeded)}

    def list_thermal_readings(self, limit=100):
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM thermal_readings ORDER BY recorded_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ------------------------------------------------------- recorded video analysis
    def analyze_recorded_video(self, filename, frames, source_kind, model_id=None, gate_id=None, confidence=0.25, camera_id=None):
        """Analyze a list of already-decoded frames and persist the results.

        `frames` is a list of BGR numpy frames. This method runs genuine model
        inference, classifies entry/exit direction per track, records access
        events, and returns a summary that is stored in `video_analyses`.
        """
        if source_kind == "thermal":
            model_id = model_id or THERMAL_MODEL_ID
        else:
            model_id = model_id or CCTV_MODEL_ID

        analysis_id = f"VDA-{uuid.uuid4().hex[:10].upper()}"
        detections_total = 0
        people_max = 0
        entry_count = 0
        exit_count = 0
        temperature_measurements = 0
        events_created = 0
        people_tracks = set()
        tracker = self._event_engine.tracker
        gate = self._gate_for_camera(camera_id) if camera_id else None

        for frame in frames:
            ok, buffer = cv2.imencode(".jpg", frame)
            if not ok:
                continue
            image_bytes = buffer.tobytes()
            try:
                result = inference_service.detect_image(image_bytes, model_id, conf=confidence)
            except Exception:
                continue
            if result.get("status") != "SUCCESS":
                continue
            detections = [d for d in result.get("detections", []) if (d.get("class_name") or "").lower() == PERSON_CLASS]
            detections_total += len(result.get("detections", []))
            current_people = len(detections)
            people_max = max(people_max, current_people)
            # Persist tracks across frames with the shared temporal tracker so
            # direction can be derived from genuine frame-to-frame movement.
            detections = tracker.update(detections, camera_id or "recorded")
            for detection in detections:
                track_id = detection.get("track_id")
                if track_id:
                    people_tracks.add(track_id)
                point = _center(detection.get("bbox", [0, 0, 0, 0]))
                direction = None
                if gate and track_id:
                    direction = self._tracker.update(track_id, point, gate)
                if direction:
                    evidence_id = self._save_evidence(frame, camera_id=camera_id, source_kind=source_kind)
                    record = self.record_access(
                        direction=direction,
                        camera_id=camera_id,
                        track_id=track_id,
                        gate_id=gate.get("gate_id") if gate else gate_id,
                        confidence=detection.get("confidence", 0.0),
                        evidence_id=evidence_id,
                        metadata={"source_kind": source_kind, "analysis_id": analysis_id, "verification_source": None, "evidence_id": evidence_id},
                    )
                    if direction == ENTRY:
                        entry_count += 1
                    else:
                        exit_count += 1
                    if record.get("id"):
                        events_created += 1

        model_row = inference_service.model_status(model_id)
        notes = "Recorded video analysis only. Not a live feed."
        if model_row.get("status") != "AVAILABLE":
            notes += f" Model '{model_id}' is not trained/deployed; no detections were produced."

        metadata = {
            "source_kind": source_kind,
            "model_id": model_id,
            "model_status": model_row.get("status"),
            "track_count": len(people_tracks),
        }
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO video_analyses
            (id, camera_id, camera_name, source_kind, filename, model_id, frames_processed,
             detections_count, events_created, people_max, entry_count, exit_count,
             temperature_measurements, status, notes, metadata_json, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECORDED', ?, ?, ?)
            """,
            (
                analysis_id,
                camera_id,
                filename,
                source_kind,
                filename,
                model_id,
                len(frames),
                detections_total,
                events_created,
                people_max,
                entry_count,
                exit_count,
                temperature_measurements,
                notes,
                json.dumps(metadata),
                "system",
            ),
        )
        conn.commit()
        conn.close()
        return {
            "analysis_id": analysis_id,
            "source_kind": source_kind,
            "model_id": model_id,
            "model_status": model_row.get("status"),
            "frames_processed": len(frames),
            "detections_count": detections_total,
            "people_max": people_max,
            "entry_count": entry_count,
            "exit_count": exit_count,
            "events_created": events_created,
            "temperature_measurements": temperature_measurements,
            "notes": notes,
        }

    def list_video_analyses(self, limit=100):
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM video_analyses ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        analyses = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _safe_json_loads(item.pop("metadata_json"), {})
            analyses.append(item)
        return analyses

    # ------------------------------------------------------------------ stats
    def dashboard_stats(self):
        conn = get_db_connection()
        stats = {}
        stats["entry_total"] = conn.execute("SELECT COUNT(*) FROM access_records WHERE direction = 'ENTRY'").fetchone()[0]
        stats["exit_total"] = conn.execute("SELECT COUNT(*) FROM access_records WHERE direction = 'EXIT'").fetchone()[0]
        stats["pending_verification"] = conn.execute("SELECT COUNT(*) FROM access_records WHERE verification_status = 'UNVERIFIED'").fetchone()[0]
        stats["asset_movements"] = conn.execute("SELECT COUNT(*) FROM asset_movements").fetchone()[0]
        stats["unmatched_assets"] = conn.execute("SELECT COUNT(*) FROM asset_movements WHERE verification_status = 'UNMATCHED'").fetchone()[0]
        stats["thermal_readings"] = conn.execute("SELECT COUNT(*) FROM thermal_readings").fetchone()[0]
        stats["thermal_alerts"] = conn.execute("SELECT COUNT(*) FROM thermal_readings WHERE threshold_exceeded = 1").fetchone()[0]
        stats["video_analyses"] = conn.execute("SELECT COUNT(*) FROM video_analyses").fetchone()[0]
        stats["assets_total"] = conn.execute("SELECT COUNT(*) FROM asset_register").fetchone()[0]
        stats["cctv_cameras"] = conn.execute("SELECT COUNT(*) FROM cameras WHERE LOWER(source_type) IN ('rtsp','onvif','http','webcam','cctv','cctv_rtsp')").fetchone()[0]
        stats["thermal_cameras"] = conn.execute("SELECT COUNT(*) FROM cameras WHERE LOWER(source_type) IN ('thermal','thermal_rtsp')").fetchone()[0]
        stats["cameras_online"] = conn.execute("SELECT COUNT(*) FROM cameras WHERE status = 'ONLINE'").fetchone()[0]
        conn.close()
        return stats

    def security_events(self, limit=100):
        """Aggregate access-control and asset exceptions for human review."""
        conn = get_db_connection()
        events = []
        unverified = conn.execute(
            "SELECT id, direction, gate_id, camera_id, recorded_at FROM access_records WHERE verification_status = 'UNVERIFIED' ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        for row in unverified:
            events.append(
                {
                    "type": "UNVERIFIED_ENTRY" if row["direction"] == ENTRY else "UNVERIFIED_EXIT",
                    "severity": "MEDIUM",
                    "ref": row["id"],
                    "gate_id": row["gate_id"],
                    "camera_id": row["camera_id"],
                    "recorded_at": row["recorded_at"],
                }
            )
        unmatched = conn.execute(
            "SELECT id, asset_id, gate_id, camera_id, moved_at FROM asset_movements WHERE verification_status = 'UNMATCHED' ORDER BY moved_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        for row in unmatched:
            events.append(
                {
                    "type": "UNMATCHED_ASSET_MOVEMENT",
                    "severity": "HIGH",
                    "ref": row["id"],
                    "asset_id": row["asset_id"],
                    "gate_id": row["gate_id"],
                    "camera_id": row["camera_id"],
                    "recorded_at": row["moved_at"],
                }
            )
        thermal = conn.execute(
            "SELECT id, camera_id, temperature_c, threshold_c, recorded_at FROM thermal_readings WHERE threshold_exceeded = 1 ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        for row in thermal:
            events.append(
                {
                    "type": "THERMAL_THRESHOLD_EXCEEDED",
                    "severity": "MEDIUM",
                    "ref": row["id"],
                    "camera_id": row["camera_id"],
                    "temperature_c": row["temperature_c"],
                    "threshold_c": row["threshold_c"],
                    "recorded_at": row["recorded_at"],
                }
            )
        conn.close()
        events.sort(key=lambda event: event.get("recorded_at") or "", reverse=True)
        return events[:limit]


security_service = SecurityMonitoringService()