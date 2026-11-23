import base64
import hashlib
import json
import math
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from config import ALERT_DEDUP_SECONDS, EVIDENCE_DIR, LEAKAGE_COOLDOWN_SECONDS, LEAKAGE_MIN_FRAMES
from database import get_db_connection, now_iso
from inference_service import inference_service

LEAKAGE_CLASSES = {
    "water dripping",
    "water spraying",
    "visible water flow",
    "pipe joint leakage",
    "water accumulation",
    "water leak",
    "leak",
    "standing water",
}

SAFETY_CLASS_MAP = {
    "cell phone": "MOBILE_PHONE_USAGE",
    "person": "PERSON_DETECTED",
    "truck": "VEHICLE_DETECTED",
    "car": "VEHICLE_DETECTED",
    "motorcycle": "VEHICLE_DETECTED",
    "bus": "VEHICLE_DETECTED",
}


def _point_in_polygon(point, polygon):
    x, y = point
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    p1x, p1y = polygon[0]
    for i in range(n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def _center(bbox):
    return bbox[0] + bbox[2] / 2.0, bbox[1] + bbox[3] / 2.0


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class SimpleTracker:
    def __init__(self, max_distance=0.22, max_stale_seconds=4.0):
        self.tracks = {}
        self.counter = 0
        self.max_distance = max_distance
        self.max_stale_seconds = max_stale_seconds

    def update(self, detections, camera_id):
        now = time.time()
        for track in list(self.tracks.values()):
            if now - track["last_seen"] > self.max_stale_seconds:
                del self.tracks[track["id"]]
        used = set()
        for detection in detections:
            bbox = detection["bbox"]
            center = _center(bbox)
            candidates = []
            for track_id, track in self.tracks.items():
                if track["camera_id"] != camera_id or track["class_name"] != detection["class_name"]:
                    continue
                if track_id in used:
                    continue
                distance = _distance(center, track["center"])
                if distance <= self.max_distance:
                    candidates.append((distance, track_id))
            if candidates:
                _, track_id = min(candidates)
                track = self.tracks[track_id]
            else:
                self.counter += 1
                track_id = f"TRK-{self.counter:05d}"
                self.tracks[track_id] = {
                    "id": track_id,
                    "camera_id": camera_id,
                    "class_name": detection["class_name"],
                    "center": center,
                    "bbox": bbox,
                    "confidence": detection["confidence"],
                    "hits": 0,
                    "first_seen": now,
                    "last_seen": now,
                }
            used.add(track_id)
            track = self.tracks[track_id]
            track.update(
                {
                    "center": center,
                    "bbox": bbox,
                    "confidence": detection["confidence"],
                    "hits": track["hits"] + 1,
                    "last_seen": now,
                }
            )
            detection["track_id"] = track_id
            detection["track_hits"] = track["hits"]
        return detections


class EventEngine:
    def __init__(self):
        self.tracker = SimpleTracker()
        self.last_alert_at = {}
        self._lock = __import__("threading").RLock()

    def _camera(self, camera_id):
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def _zones(self, camera_id):
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM monitoring_zones WHERE camera_id = ?", (camera_id,)).fetchall()
        conn.close()
        zones = []
        for row in rows:
            item = dict(row)
            try:
                item["polygon_coords"] = json.loads(item["polygon_coords"] or "[]")
                item["model_ids"] = json.loads(item["model_ids"] or "[]")
                item["settings_json"] = json.loads(item["settings_json"] or "{}")
            except Exception:
                item["polygon_coords"] = []
                item["model_ids"] = []
                item["settings_json"] = {}
            zones.append(item)
        return zones

    def _zone_for(self, detection, zones):
        center = _center(detection["bbox"])
        for zone in zones:
            if zone.get("zone_type") == "MONITORING" and _point_in_polygon(center, zone.get("polygon_coords", [])):
                return zone
        return None

    def _restricted_for(self, detection, zones):
        center = _center(detection["bbox"])
        for zone in zones:
            if zone.get("zone_type") == "RESTRICTED" and _point_in_polygon(center, zone.get("polygon_coords", [])):
                return zone
        return None

    def _camera_config(self, camera):
        try:
            return json.loads(camera.get("config_json") or "{}")
        except Exception:
            return {}

    def _model_ids(self, camera):
        config = self._camera_config(camera)
        model_ids = config.get("model_ids") or []
        if isinstance(model_ids, str):
            model_ids = [model_ids]
        return model_ids or ["model-yolov8n"]

    def _thresholds(self, camera, event_type):
        config = self._camera_config(camera)
        defaults = config.get("thresholds", {}) if isinstance(config.get("thresholds"), dict) else {}
        return {
            "min_confidence": float(defaults.get("min_confidence", 0.35)),
            "min_frames": int(defaults.get("min_frames", LEAKAGE_MIN_FRAMES if "WATER" in event_type else 2)),
            "cooldown_seconds": int(defaults.get("cooldown_seconds", LEAKAGE_COOLDOWN_SECONDS)),
        }

    def _save_evidence(self, image_bytes, camera_id, event_id):
        if not image_bytes:
            return None
        try:
            digest = hashlib.sha256(image_bytes).hexdigest()
            evidence_id = f"EVD-{uuid.uuid4().hex[:10].upper()}"
            filename = f"{evidence_id}.jpg"
            path = Path(EVIDENCE_DIR) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_bytes)
            conn = get_db_connection()
            width = height = None
            image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
            if image is not None:
                height, width = image.shape[:2]
            conn.execute(
                """
                INSERT INTO evidence_files (id, event_id, camera_id, file_path, content_type, width, height, sha256, retention_days)
                VALUES (?, ?, ?, ?, 'image/jpeg', ?, ?, ?, 90)
                """,
                (evidence_id, event_id, camera_id, str(path), width, height, digest),
            )
            conn.commit()
            conn.close()
            return evidence_id
        except Exception as exc:
            print(f"[EVENT ENGINE] Evidence capture failed: {exc}")
            return None

    def _evidence_base64(self, image_bytes):
        if not image_bytes or len(image_bytes) > 8 * 1024 * 1024:
            return ""
        return base64.b64encode(image_bytes).decode("ascii")

    def _duplicate(self, camera_id, event_type, zone_id, dedup_key, cooldown_seconds):
        conn = get_db_connection()
        row = conn.execute(
            """
            SELECT id, created_at FROM detection_events
            WHERE camera_id = ? AND event_type = ? AND IFNULL(zone_id, '') = IFNULL(?, '')
              AND dedup_key = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (camera_id, event_type, zone_id, dedup_key),
        ).fetchone()
        conn.close()
        if not row:
            return False
        try:
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        except Exception:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - created).total_seconds() < cooldown_seconds

    def _alert_duplicate(self, camera_id, event_type, dedup_key):
        conn = get_db_connection()
        row = conn.execute(
            """
            SELECT detected_at FROM alerts
            WHERE camera_id = ? AND event_type = ? AND dedup_key = ?
            ORDER BY detected_at DESC LIMIT 1
            """,
            (camera_id, event_type, dedup_key),
        ).fetchone()
        conn.close()
        if not row:
            return False
        try:
            detected = datetime.fromisoformat(str(row["detected_at"]).replace("Z", "+00:00"))
        except Exception:
            return False
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - detected).total_seconds() < ALERT_DEDUP_SECONDS

    def _create_detection_event(self, camera, zone, detection, event_type, severity, evidence_id, dedup_key, extra=None):
        event_id = f"EVT-{uuid.uuid4().hex[:10].upper()}"
        metadata = {
            "class_name": detection.get("class_name"),
            "track_id": detection.get("track_id"),
            "track_hits": detection.get("track_hits", 1),
            "bbox": detection.get("bbox"),
            "model_id": extra.get("model_id") if extra else None,
            "zone_type": zone.get("zone_type") if zone else None,
            "source": "backend-inference",
        }
        if extra:
            metadata.update(extra.get("metadata", {}))
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO detection_events
            (id, camera_id, plant_location_id, pipeline_id, zone_id, model_id, event_type,
             severity, status, confidence, bbox_json, frame_timestamp, evidence_id, dedup_key, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DETECTED', ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                camera["id"],
                camera.get("plant_location_id"),
                camera.get("pipeline_id"),
                zone.get("id") if zone else None,
                extra.get("model_id") if extra else None,
                event_type,
                severity,
                detection.get("confidence", 0.0),
                json.dumps(detection.get("bbox")),
                now_iso(),
                evidence_id,
                dedup_key,
                json.dumps(metadata),
            ),
        )
        conn.commit()
        conn.close()
        return event_id, metadata

    def _create_incident(self, camera, zone, event_id, event_type, severity, confidence, evidence_id):
        incident_id = f"INC-{uuid.uuid4().hex[:10].upper()}"
        conn = get_db_connection()
        if event_type == "WATER_LEAKAGE":
            conn.execute(
                """
                INSERT INTO leakage_incidents
                (id, event_id, camera_id, pipeline_id, zone_id, status, confidence, evidence_id)
                VALUES (?, ?, ?, ?, ?, 'POSSIBLE', ?, ?)
                """,
                (incident_id, event_id, camera["id"], camera.get("pipeline_id"), zone.get("id") if zone else None, confidence, evidence_id),
            )
        else:
            category = event_type
            conn.execute(
                """
                INSERT INTO safety_incidents
                (id, event_id, camera_id, zone_id, category, status, confidence, evidence_id)
                VALUES (?, ?, ?, ?, ?, 'DETECTED', ?, ?)
                """,
                (incident_id, event_id, camera["id"], zone.get("id") if zone else None, category, confidence, evidence_id),
            )
        conn.commit()
        conn.close()
        return incident_id

    def _create_alert(self, camera, zone, event_type, severity, confidence, evidence_base64, metadata, event_id):
        alert_id = f"ALT-{uuid.uuid4().hex[:8].upper()}"
        dedup_key = metadata.get("dedup_key", event_id)
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO alerts
            (id, event_id, event_type, camera_id, camera_name, zone_name, severity, status,
             confidence, evidence_image_base64, metadata_json, dedup_key, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'DETECTED', ?, ?, ?, ?, ?)
            """,
            (
                alert_id,
                event_id,
                event_type,
                camera["id"],
                camera.get("name") or camera["id"],
                zone.get("zone_name") if zone else "General Area",
                severity,
                confidence,
                evidence_base64,
                json.dumps(metadata),
                dedup_key,
                now_iso(),
            ),
        )
        conn.commit()
        conn.close()
        return alert_id

    def process_image(self, camera_id, image_bytes, model_id=None, conf=None, temporal=False, zone_id=None):
        camera = self._camera(camera_id)
        if not camera:
            raise ValueError("Camera not found")
        model_ids = [model_id] if model_id else self._model_ids(camera)
        zones = self._zones(camera_id)
        selected_zone = next((z for z in zones if z["id"] == zone_id), None)
        if selected_zone:
            zones = [selected_zone]
        results = []
        all_detections = []
        for model in model_ids:
            try:
                result = inference_service.detect_image(image_bytes, model, conf=conf if conf is not None else 0.25)
            except Exception as exc:
                results.append({"model_id": model, "status": "ERROR", "message": str(exc)})
                continue
            if result.get("status") != "SUCCESS":
                results.append(result)
                continue
            all_detections.extend(result["detections"])
        if temporal:
            all_detections = self.tracker.update(all_detections, camera_id)
        events = []
        for detection_index, detection in enumerate(all_detections):
            if not temporal and not detection.get("track_id"):
                detection["track_id"] = f"FRAME-{detection_index + 1}"
            class_name = (detection.get("class_name") or "").lower()
            if class_name in SAFETY_CLASS_MAP:
                event_type = SAFETY_CLASS_MAP[class_name]
            elif class_name in LEAKAGE_CLASSES:
                event_type = "WATER_LEAKAGE"
            else:
                continue
            zone = self._zone_for(detection, zones)
            if selected_zone and zone is None:
                continue
            restricted = self._restricted_for(detection, zones)
            if restricted and class_name in {"person", "truck", "car", "motorcycle", "bus"}:
                event_type = "RESTRICTED_ZONE"
                zone = restricted
            thresholds = self._thresholds(camera, event_type)
            confidence = float(detection.get("confidence", 0.0))
            if confidence < thresholds["min_confidence"]:
                continue
            track_hits = int(detection.get("track_hits", 1))
            if temporal and track_hits < thresholds["min_frames"]:
                continue
            identity = class_name if event_type == "WATER_LEAKAGE" else (detection.get("track_id") or class_name)
            dedup_key = f"{camera_id}:{event_type}:{zone.get('id') if zone else ''}:{identity}"
            if self._duplicate(camera_id, event_type, zone.get("id") if zone else None, dedup_key, thresholds["cooldown_seconds"]):
                events.append({"status": "DUPLICATE", "event_type": event_type, "track_id": detection.get("track_id")})
                continue
            evidence_id = None
            evidence_base64 = ""
            if event_type == "WATER_LEAKAGE":
                severity = "HIGH" if class_name in {"pipe joint leakage", "water spraying"} else "MEDIUM"
            elif event_type in {"RESTRICTED_ZONE", "MOBILE_PHONE_USAGE"}:
                severity = "HIGH"
            else:
                severity = "LOW"
            event_id, metadata = self._create_detection_event(
                camera,
                zone,
                detection,
                event_type,
                severity,
                evidence_id,
                dedup_key,
                {"model_id": model_id or self._model_ids(camera)[0], "metadata": {"dedup_key": dedup_key}},
            )
            evidence_id = self._save_evidence(image_bytes, camera_id, event_id)
            evidence_base64 = self._evidence_base64(image_bytes)
            if evidence_id:
                conn = get_db_connection()
                conn.execute("UPDATE detection_events SET evidence_id = ?, updated_at = ? WHERE id = ?", (evidence_id, now_iso(), event_id))
                conn.commit()
                conn.close()
            should_alert = event_type not in {"PERSON_DETECTED", "VEHICLE_DETECTED"} or bool(restricted)
            incident_id = None
            alert_id = None
            if should_alert:
                incident_id = self._create_incident(camera, zone, event_id, event_type, severity, confidence, evidence_id)
                metadata["dedup_key"] = dedup_key
                metadata["incident_id"] = incident_id
                if not self._alert_duplicate(camera_id, event_type, dedup_key):
                    alert_id = self._create_alert(camera, zone, event_type, severity, confidence, evidence_base64, metadata, event_id)
            events.append(
                {
                    "status": "CREATED",
                    "event_id": event_id,
                    "incident_id": incident_id,
                    "alert_id": alert_id,
                    "event_type": event_type,
                    "severity": severity,
                    "confidence": confidence,
                    "track_id": detection.get("track_id"),
                    "evidence_id": evidence_id,
                }
            )
        return {
            "camera_id": camera_id,
            "models": results,
            "detections_count": len(all_detections),
            "events": events,
        }

    def recent_events(self, limit=100, event_type=None, status=None):
        conn = get_db_connection()
        query = "SELECT * FROM detection_events"
        clauses = []
        params = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, tuple(params)).fetchall()
        conn.close()
        return [dict(row) for row in rows]


event_engine = EventEngine()