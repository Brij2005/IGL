"""Safety event orchestration: real detections in, audited events out.

This is the runtime junction the platform was missing. It sits directly behind
the inference pipeline and performs, in order:

1. Persist the model's raw detections and their visual tracks.
2. Evaluate every configured detector that this build actually implements,
   using only real detections and configured zone geometry.
3. Run each observation through the temporal verifier, whose thresholds come
   from configuration.
4. Create or update an Event only from a real positive observation, recording
   detector, verification state, thresholds, model name and weights checksum.
5. Capture evidence from the very frame that produced the observation.
6. Correlate the event with persisted events, and notify only when a policy
   says so.

Anything it cannot evaluate is reported with an explicit state. It never creates
an event from configuration, from a health check, or from a threshold.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.models import Camera, Detection, DetectorConfig, Event, EventEvidence, Notification, OperatingThreshold, Role, Track, User, Zone
    from app.services.correlation_engine import correlate_event
    from app.services.escalation_engine import evaluate_event_escalation, notification_targets
    from app.services.evidence_engine import evidence_engine
    from app.services.notification_engine import enqueue_notification
    from app.services.safety_engine import (
        ENGINEERING_DEFAULT,
        detector_capabilities,
        default_severity,
        evaluate_frame_class_detector,
        evaluate_restricted_zone,
        event_type_for,
        temporal_registry,
        verification_policy_from_configuration,
    )
except ImportError:  # pragma: no cover
    from backend.app.config import settings
    from backend.app.models import Camera, Detection, DetectorConfig, Event, EventEvidence, Notification, OperatingThreshold, Role, Track, User, Zone
    from backend.app.services.correlation_engine import correlate_event
    from backend.app.services.escalation_engine import evaluate_event_escalation, notification_targets
    from backend.app.services.evidence_engine import evidence_engine
    from backend.app.services.notification_engine import enqueue_notification
    from backend.app.services.safety_engine import (
        ENGINEERING_DEFAULT,
        detector_capabilities,
        default_severity,
        evaluate_frame_class_detector,
        evaluate_restricted_zone,
        event_type_for,
        temporal_registry,
        verification_policy_from_configuration,
    )


logger = logging.getLogger("igl.safety.orchestrator")

# Open event states that may still receive verification updates.
OPEN_WORKFLOW_STATES = {
    "NEW",
    "UNACKNOWLEDGED",
    "ACKNOWLEDGED",
    "ASSIGNED",
    "UNDER_INVESTIGATION",
    "ACTION_REQUIRED",
}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SafetyEventOrchestrator:
    """Evaluates configured detectors and persists whatever they actually observe."""

    def __init__(self, capture_evidence: bool = True) -> None:
        self.capture_evidence = capture_evidence

    # ------------------------------------------------------------------
    # Detection and track persistence
    # ------------------------------------------------------------------
    def persist_detections(
        self,
        db: Session,
        camera: Camera,
        detections: Sequence[Any],
        tracks: Sequence[Any],
    ) -> Dict[str, Any]:
        """Persist raw model output. A single-frame detection is POSSIBLE at most."""
        current_tracks = [
            track for track in tracks
            if getattr(track, "lifecycle_state", None) in ("DETECTED", "TRACKED")
        ]
        matched_track_ids: set[str] = set()
        rows: List[Detection] = []
        for detection in detections:
            track = next((
                candidate for candidate in current_tracks
                if candidate.track_id not in matched_track_ids
                and candidate.class_name == detection.class_name
                and tuple(candidate.bbox) == tuple(detection.bbox)
            ), None)
            track_row = None
            if track is not None and track.track_id:
                matched_track_ids.add(track.track_id)
                track_row = (
                    db.query(Track)
                    .filter(Track.camera_id == camera.id, Track.track_uuid == track.track_id)
                    .first()
                )
                if track_row is None:
                    track_row = Track(
                        camera_id=camera.id,
                        track_uuid=track.track_id,
                        object_class=str(track.class_name)[:50],
                        first_seen_at=_as_utc(track.first_seen),
                        last_seen_at=_as_utc(track.last_seen),
                        # employee_id stays NULL: a visual track is not a person.
                        employee_id=None,
                        metadata_json={"lifecycle_state": track.lifecycle_state},
                    )
                    db.add(track_row)
                    db.flush()
                else:
                    track_row.last_seen_at = _as_utc(track.last_seen)
                    track_row.metadata_json = {"lifecycle_state": track.lifecycle_state}

            rows.append(
                Detection(
                    camera_id=camera.id,
                    track_id=track_row.id if track is not None and track_row is not None else None,
                    timestamp=_as_utc(detection.timestamp),
                    object_class=str(detection.class_name)[:50],
                    confidence=float(detection.confidence),
                    bbox_json=[float(value) for value in detection.bbox],
                    observation_state="POSSIBLE",
                    metadata_json={
                        "model_name": detection.model_name,
                        "model_version": detection.model_version,
                        "source": "REAL_FRAME_DETECTION",
                    },
                )
            )
        if rows:
            db.add_all(rows)
            db.commit()
            for row in rows:
                db.refresh(row)
        return {"detections_persisted": len(rows), "tracks_seen": len(current_tracks)}

    # ------------------------------------------------------------------
    # Detector evaluation
    # ------------------------------------------------------------------
    def evaluate_frame(
        self,
        db: Session,
        camera: Camera,
        *,
        timestamp: datetime,
        frame: Optional[np.ndarray],
        detections: Sequence[Any],
        tracks: Sequence[Any],
        model_health: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate every applicable detector for one real frame."""
        zone = db.query(Zone).filter(Zone.id == camera.zone_id).first() if camera.zone_id else None
        configs = (
            db.query(DetectorConfig)
            .filter(DetectorConfig.is_enabled.is_(True))
            .filter(
                (DetectorConfig.camera_id == camera.id)
                | ((DetectorConfig.camera_id.is_(None)) & (DetectorConfig.zone_id == camera.zone_id))
            )
            .all()
        ) if camera.zone_id else (
            db.query(DetectorConfig)
            .filter(DetectorConfig.is_enabled.is_(True), DetectorConfig.camera_id == camera.id)
            .all()
        )
        thresholds = (
            db.query(OperatingThreshold)
            .filter(
                (OperatingThreshold.camera_id == camera.id)
                | (OperatingThreshold.zone_id == camera.zone_id)
            )
            .all()
        )

        capabilities = {capability.detector_key: capability for capability in detector_capabilities(model_health, configs)}
        frame_height, frame_width = (int(frame.shape[0]), int(frame.shape[1])) if isinstance(frame, np.ndarray) and frame.size else (0, 0)

        observations = []
        for config in configs:
            detector_key = config.detector_key
            capability = capabilities.get(detector_key)
            if capability is None or not capability.operational:
                observations.append(
                    {
                        "detector_key": detector_key,
                        "state": "SKIPPED",
                        "reason": capability.reason if capability else "Detector is not part of this build",
                        "event_created": False,
                    }
                )
                continue

            if detector_key == "RESTRICTED_ZONE":
                if not tracks:
                    observations.append(
                        {
                            "detector_key": detector_key,
                            "state": "NOT_ASSESSABLE",
                            "reason": "No tracked object was produced for this frame",
                            "event_created": False,
                        }
                    )
                    continue
                for track in tracks:
                    observations.append(
                        self._observation_entry(
                            evaluate_restricted_zone(
                                camera=camera,
                                zone=zone,
                                tracked_object=track,
                                frame_width=frame_width,
                                frame_height=frame_height,
                            )
                        )
                    )
            else:
                minimum_confidence, threshold_source, source_reference = self._confidence_floor(
                    config, thresholds, detector_key, model_health
                )
                observations.append(
                    self._observation_entry(
                        evaluate_frame_class_detector(
                            detector_key,
                            detections=detections,
                            model_classes=model_health.get("classes") or (),
                            minimum_confidence=minimum_confidence,
                            threshold_source=threshold_source,
                            source_reference=source_reference,
                        )
                    )
                )

        created_or_updated = []
        for entry in observations:
            if entry.get("state") == "SKIPPED" or not entry.get("condition_met"):
                continue
            outcome = self._apply_observation(
                db=db,
                camera=camera,
                zone=zone,
                timestamp=timestamp,
                frame=frame,
                observation=entry,
                model_health=model_health,
                configs=configs,
                thresholds=thresholds,
            )
            entry["event_created"] = outcome["event_created"]
            entry["event_id"] = outcome.get("event_id")
            entry["verification_state"] = outcome.get("verification_state")
            created_or_updated.append(outcome)

        return {
            "camera_id": camera.id,
            "timestamp": timestamp,
            "frame_size": [frame_width, frame_height],
            "model_status": model_health.get("status"),
            "detectors_evaluated": [entry["detector_key"] for entry in observations],
            "observations": observations,
            "events": created_or_updated,
        }

    @staticmethod
    def _observation_entry(observation) -> Dict[str, Any]:
        return {
            "detector_key": observation.detector_key,
            "condition_met": observation.condition_met,
            "state": observation.observation_state,
            "confidence": observation.confidence,
            "reason": observation.reason,
            "subject_id": observation.subject_id,
            "track_id": observation.track_id,
            "zone_id": observation.zone_id,
            "object_class": observation.object_class,
            "bbox": list(observation.bbox) if observation.bbox else None,
            "threshold_source": observation.threshold_source,
            "observation": observation,
        }

    @staticmethod
    def _confidence_floor(
        config: DetectorConfig,
        thresholds: Sequence[OperatingThreshold],
        detector_key: str,
        model_health: Dict[str, Any],
    ) -> tuple[float, str, Optional[str]]:
        parameters = config.parameters_json or {}
        if "minimum_confidence" in parameters:
            source = config.threshold_source
            reference = config.source_reference
            if source == "CONFIGURED" and not reference:
                source = ENGINEERING_DEFAULT
            return float(parameters["minimum_confidence"]), source, reference
        for threshold in thresholds:
            if threshold.code == f"{detector_key}.minimum_confidence":
                return (
                    float(threshold.value),
                    threshold.threshold_source,
                    threshold.source_reference,
                )
        return (
            float(model_health.get("confidence_threshold") or settings.MODEL_CONFIDENCE_THRESHOLD),
            ENGINEERING_DEFAULT,
            None,
        )

    # ------------------------------------------------------------------
    # Temporal verification and event lifecycle
    # ------------------------------------------------------------------
    def _apply_observation(
        self,
        *,
        db: Session,
        camera: Camera,
        zone: Optional[Zone],
        timestamp: datetime,
        frame: Optional[np.ndarray],
        observation: Dict[str, Any],
        model_health: Dict[str, Any],
        configs: Sequence[DetectorConfig],
        thresholds: Sequence[OperatingThreshold],
    ) -> Dict[str, Any]:
        detector_key = observation["detector_key"]
        domain_observation = observation["observation"]
        policy = verification_policy_from_configuration(detector_key, configs, thresholds)
        subject = observation.get("subject_id") or "frame"
        key = f"{camera.id}|{detector_key}|{subject}"
        verifier = temporal_registry.verifier_for(key, policy)
        result = verifier.observe(
            subject_id=subject,
            timestamp=timestamp,
            positive=True,
            confidence=observation.get("confidence"),
        )

        track_row = None
        if observation.get("track_id"):
            track_row = (
                db.query(Track)
                .filter(Track.camera_id == camera.id, Track.track_uuid == observation["track_id"])
                .first()
            )

        event = self._find_open_event(db, camera.id, detector_key, observation.get("track_id"))
        provenance = {
            "detector_reason": observation["reason"],
            "object_class": observation.get("object_class"),
            "zone_code": getattr(zone, "code", None),
            "verification": {
                "state": result.state,
                "observations": result.observation_count,
                "duration_seconds": result.duration_seconds,
                "threshold_source": result.threshold_source,
                "rule_reference": result.rule_reference,
            },
            "model": {
                "name": model_health.get("model_name"),
                "version": model_health.get("model_version"),
                "weights_checksum_sha256": model_health.get("weights_checksum_sha256"),
            },
            "frame_size": list(frame.shape[:2]) if isinstance(frame, np.ndarray) and frame.size else None,
        }

        if event is None:
            event = Event(
                camera_id=camera.id,
                zone_id=observation.get("zone_id") or camera.zone_id,
                track_id=track_row.id if track_row else None,
                event_type=event_type_for(detector_key),
                observation_state="CONFIRMED" if result.state == "VERIFIED" else "POSSIBLE",
                severity=default_severity(detector_key),
                workflow_state="NEW",
                confidence=observation.get("confidence"),
                duration_seconds=result.duration_seconds or None,
                started_at=timestamp,
                model_version=model_health.get("model_version"),
                detector_key=detector_key,
                verification_state=result.state,
                temporal_observations=result.observation_count,
                temporal_duration_seconds=result.duration_seconds or None,
                threshold_source=result.threshold_source,
                source_reference=result.rule_reference,
                model_name=model_health.get("model_name"),
                model_weights_checksum=model_health.get("weights_checksum_sha256"),
                provenance_json=provenance,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            event_created = True
        else:
            event.verification_state = result.state
            event.temporal_observations = result.observation_count
            event.temporal_duration_seconds = result.duration_seconds or None
            event.duration_seconds = result.duration_seconds or None
            event.observation_state = "CONFIRMED" if result.state == "VERIFIED" else event.observation_state
            if observation.get("confidence") is not None:
                event.confidence = observation["confidence"]
            event.provenance_json = provenance
            db.commit()
            db.refresh(event)
            event_created = False

        correlation = correlate_event(db, event, self._correlation_window(configs, thresholds, detector_key))
        evidence_status = "EVIDENCE_CAPTURE_DISABLED"
        if self.capture_evidence and observation.get("confidence") is not None:
            existing_evidence = db.query(EventEvidence.id).filter(EventEvidence.event_id == event.id).first()
            if existing_evidence is not None:
                evidence_status = "EVIDENCE_ALREADY_CAPTURED"
            else:
                captured = evidence_engine.capture_snapshot(
                    db,
                    event_id=event.id,
                    camera_id=camera.id,
                    frame_timestamp=timestamp,
                    frame=frame,
                )
                evidence_status = captured.status
                if captured.evidence is not None:
                    captured.evidence.camera_id = camera.id
                    captured.evidence.size_bytes = self._file_size(captured.evidence.file_path)
                    db.commit()

        notifications = self._notify_policy_targets(db, event, observation)
        escalation = evaluate_event_escalation(db, event, now=timestamp)

        return {
            "event_id": event.id,
            "event_created": event_created,
            "verification_state": result.state,
            "observations": result.observation_count,
            "duration_seconds": result.duration_seconds,
            "correlation_key": correlation.correlation_key,
            "evidence_status": evidence_status,
            "notifications_created": len(notifications),
            "escalation_status": escalation["status"],
        }

    @staticmethod
    def _correlation_window(
        configs: Sequence[DetectorConfig],
        thresholds: Sequence[OperatingThreshold],
        detector_key: str,
    ) -> int:
        for config in configs:
            if config.detector_key != detector_key:
                continue
            parameters = config.parameters_json or {}
            if "correlation_window_seconds" in parameters:
                return max(1, int(parameters["correlation_window_seconds"]))
        for threshold in thresholds:
            if threshold.code == f"{detector_key}.correlation_window_seconds":
                return max(1, int(threshold.value))
        return settings.EVENT_CORRELATION_WINDOW_SECONDS

    @staticmethod
    def _find_open_event(db: Session, camera_id: str, detector_key: str, track_uuid: Optional[str]) -> Optional[Event]:
        """Find the event this observation continues, if any.

        Repeat protection: an open event for the same camera, detector and
        tracked object is updated instead of creating a duplicate row for every
        frame.
        """
        query = (
            db.query(Event)
            .filter(
                Event.camera_id == camera_id,
                Event.detector_key == detector_key,
                Event.workflow_state.in_(sorted(OPEN_WORKFLOW_STATES)),
                Event.ended_at.is_(None),
            )
            .order_by(Event.started_at.desc())
        )
        if track_uuid:
            track = db.query(Track).filter(Track.camera_id == camera_id, Track.track_uuid == track_uuid).first()
            if track is not None:
                event = query.filter(Event.track_id == track.id).first()
                if event is not None:
                    return event
        return query.first()

    @staticmethod
    def _notify_policy_targets(db: Session, event: Event, observation: Dict[str, Any]) -> List[Notification]:
        """Create notifications only where an operator policy actually applies.

        The audience is the policy's recipient role. With no authenticated
        session there is no "current user" to notify, so the row is addressed to
        the role itself and any matching named identities receive their own row.
        """
        created: List[Notification] = []
        for policy in notification_targets(db, event):
            if observation.get("confidence") is None:
                # Nothing was measured, so there is nothing to alert about.
                continue
            named_identities = (
                db.query(User)
                .join(Role, User.role_id == Role.id)
                .filter(Role.name == policy.recipient_role, User.is_active.is_(True))
                .all()
            )
            audiences: List[dict[str, Any]] = [
                {
                    "user_id": None,
                    "recipient_role": policy.recipient_role,
                    "recipient": policy.recipient_role,
                }
            ]
            audiences.extend(
                {"user_id": identity.id, "recipient_role": None, "recipient": identity.email or identity.username}
                for identity in named_identities
            )
            for audience in audiences:
                try:
                    created.append(
                        enqueue_notification(
                            db,
                            event_id=event.id,
                            user_id=audience["user_id"],
                            recipient_role=audience["recipient_role"],
                            recipient=audience["recipient"],
                            channel=policy.channel,
                            dedup_window_seconds=policy.dedup_window_seconds,
                            payload_summary=(
                                f"{event.event_type} on camera {event.camera_id} "
                                f"({event.observation_state})"
                            ),
                        )
                    )
                except ValueError as exc:
                    logger.warning(
                        "Notification policy produced no notification",
                        extra={
                            "component": "safety_orchestrator",
                            "event_id": event.id,
                            "reason": str(exc),
                        },
                    )
        return created

    @staticmethod
    def _file_size(file_path: str) -> Optional[int]:
        """Size of a written evidence file, or None when it cannot be read."""
        try:
            return Path(file_path).stat().st_size
        except OSError:
            return None


safety_orchestrator = SafetyEventOrchestrator()