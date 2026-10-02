"""Safety condition evaluation over real model output.

This module is the only place where a safety event can be created from a
detection. Its contract is deliberately narrow:

* A detector may only be evaluated when a real model produced the underlying
  detection. No configuration, no threshold and no operator action can create an
  event by itself.
* Every detector declares what it needs. A detector whose requirements are not
  met reports an explicit unsupported state (``NOT_IMPLEMENTED``,
  ``MODEL_NOT_CONFIGURED``, ``DETECTOR_NOT_SUPPORTED_BY_MODEL``,
  ``NOT_CONFIGURED``) and evaluates nothing.
* Absence-based reasoning (a person *without* a hard hat) is not implemented: a
  detector that can see a hat cannot prove one is missing, so PPE compliance
  reports NOT_IMPLEMENTED rather than a guess.
* Temporal thresholds come from configuration. Anything unconfigured stays at
  the engineering default and is tagged ``ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION``
  so it can never be read as an IGL-validated operating value.
* Confidence is carried through from the model's own value, or left NULL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.engine.temporal_verifier import TemporalVerifier, VerificationPolicy
    from app.models import Camera, DetectorConfig, Event, OperatingThreshold, Zone
    from app.services.zone_engine import evaluate_zone_membership
except ImportError:  # pragma: no cover - import shim for direct script use
    from backend.app.config import settings
    from backend.app.engine.temporal_verifier import TemporalVerifier, VerificationPolicy
    from backend.app.models import Camera, DetectorConfig, Event, OperatingThreshold, Zone
    from backend.app.services.zone_engine import evaluate_zone_membership


ENGINEERING_DEFAULT = "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"

# Explicit detector catalogue. ``required_classes`` is matched against the loaded
# model's own class list; a detector whose classes the model does not contain is
# unsupported by that model rather than silently producing nothing.
DETECTOR_CATALOGUE: Dict[str, Dict[str, Any]] = {
    "RESTRICTED_ZONE": {
        "description": "Person or vehicle inside a configured restricted zone polygon",
        "requirement": "A person/vehicle class plus a zone polygon configured in normalized frame coordinates",
        "required_class_groups": (("person", "worker", "human"), ("forklift", "vehicle", "truck")),
        "any_group_sufficient": True,
        "subject": "TRACK",
        "state": "IMPLEMENTED",
    },
    "FIRE": {
        "description": "Fire class detected by the configured model",
        "requirement": "A fire/flame class present in the loaded model's classes",
        "required_class_groups": (("fire", "flame"),),
        "any_group_sufficient": True,
        "subject": "FRAME",
        "state": "IMPLEMENTED",
    },
    "SMOKE": {
        "description": "Smoke class detected by the configured model",
        "requirement": "A smoke class present in the loaded model's classes",
        "required_class_groups": (("smoke", "fumes"),),
        "any_group_sufficient": True,
        "subject": "FRAME",
        "state": "IMPLEMENTED",
    },
    "PPE": {
        "description": "Personal protective equipment compliance",
        "requirement": "A model that can assess both presence and absence of the item",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "reason": "PPE compliance is an absence claim. This build has no detector that can establish that an item is missing, so no compliance verdict is produced.",
    },
    "HELMET": {
        "description": "Hard hat compliance",
        "requirement": "A model that can assess both presence and absence of a hard hat",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "reason": "Hard hat compliance is an absence claim and is not implemented in this build.",
    },
    "SAFETY_VEST": {
        "description": "High-visibility vest compliance",
        "requirement": "A model that can assess both presence and absence of a vest",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "reason": "Vest compliance is an absence claim and is not implemented in this build.",
    },
    "PROXIMITY": {
        "description": "Proximity between a person and equipment or a hazard",
        "requirement": "A calibrated camera-space scale in metres per pixel",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "reason": "Proximity needs a calibrated pixel-to-metre scale, which this deployment does not supply.",
    },
    "FALL": {
        "description": "Person fall detection",
        "requirement": "A pose model and a trained fall classifier",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "reason": "No pose or fall classifier is configured.",
    },
    "LEAKAGE": {
        "description": "Fluid or gas leakage detection",
        "requirement": "A validated leakage detection model",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "reason": "No leakage detection model is configured.",
    },
    "UNSAFE_BEHAVIOR": {
        "description": "Unsafe behaviour classification",
        "requirement": "A validated behaviour classification model",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "reason": "No behaviour classification model is configured.",
    },
}

DETECTOR_KEYS = tuple(DETECTOR_CATALOGUE)

# Class names are matched case-insensitively with separators removed, because
# model class labels vary between weight files.
def _normalize_class(value: str) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _classes_available(model_classes: Sequence[str], required: Sequence[str]) -> bool:
    normalized = {_normalize_class(item) for item in model_classes}
    return all(any(_normalize_class(candidate) in normalized for candidate in group) for group in required)


def _any_class_available(model_classes: Sequence[str], groups: Sequence[Sequence[str]]) -> bool:
    normalized = {_normalize_class(item) for item in model_classes}
    for group in groups:
        if any(_normalize_class(candidate) in normalized for candidate in group):
            return True
    return False


@dataclass(frozen=True)
class DetectorCapability:
    detector_key: str
    implementation_state: str
    availability_state: str
    reason: str
    requirement: str
    configured: bool
    required_classes: tuple[str, ...] = ()
    available_model_classes: tuple[str, ...] = ()

    @property
    def operational(self) -> bool:
        """True only when this build can and does evaluate the detector."""
        return self.implementation_state == "IMPLEMENTED" and self.availability_state == "READY"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "detector_key": self.detector_key,
            "implementation_state": self.implementation_state,
            "availability_state": self.availability_state,
            "operational": self.operational,
            "reason": self.reason,
            "requirement": self.requirement,
            "configured": self.configured,
            "required_classes": list(self.required_classes),
            "available_model_classes": list(self.available_model_classes),
        }


@dataclass
class SafetyObservation:
    """One evaluated safety condition from one real frame."""

    detector_key: str
    condition_met: bool
    observation_state: str
    confidence: Optional[float]
    reason: str
    subject_id: Optional[str] = None
    track_id: Optional[str] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    zone_id: Optional[str] = None
    object_class: Optional[str] = None
    threshold_source: str = ENGINEERING_DEFAULT
    extra: Dict[str, Any] = field(default_factory=dict)


def detector_capabilities(
    model_health: Dict[str, Any],
    configs: Optional[Sequence[DetectorConfig]] = None,
) -> List[DetectorCapability]:
    """Report what each detector can actually do right now.

    The result distinguishes "this build does not implement it", "no model is
    configured", "this model cannot support it", "nothing is configured yet" and
    "ready", so a frontend cannot present an unsupported detector as active.
    """
    model_classes = tuple(model_health.get("classes") or ())
    model_status = str(model_health.get("status") or "MODEL_NOT_CONFIGURED")
    model_available = bool(model_health.get("available"))
    configured_keys = {config.detector_key for config in (configs or ()) if config.is_enabled}

    capabilities: List[DetectorCapability] = []
    for detector_key, specification in DETECTOR_CATALOGUE.items():
        required_groups = specification["required_class_groups"]
        flat_required = tuple(group[0] for group in required_groups)
        configured = detector_key in configured_keys

        if specification["state"] != "IMPLEMENTED":
            capabilities.append(
                DetectorCapability(
                    detector_key=detector_key,
                    implementation_state="NOT_IMPLEMENTED",
                    availability_state="NOT_AVAILABLE",
                    reason=specification["reason"],
                    requirement=specification["requirement"],
                    configured=configured,
                    required_classes=flat_required,
                    available_model_classes=model_classes,
                )
            )
            continue

        if not model_available:
            capabilities.append(
                DetectorCapability(
                    detector_key=detector_key,
                    implementation_state="IMPLEMENTED",
                    availability_state=model_status if model_status != "READY" else "MODEL_NOT_CONFIGURED",
                    reason=(
                        f"No usable model is loaded (model state {model_status}); no detection can be produced"
                    ),
                    requirement=specification["requirement"],
                    configured=configured,
                    required_classes=flat_required,
                    available_model_classes=model_classes,
                )
            )
            continue

        supported = (
            _any_class_available(model_classes, required_groups)
            if specification["any_group_sufficient"]
            else _classes_available(model_classes, required_groups)
        )
        if not supported:
            capabilities.append(
                DetectorCapability(
                    detector_key=detector_key,
                    implementation_state="IMPLEMENTED",
                    availability_state="DETECTOR_NOT_SUPPORTED_BY_MODEL",
                    reason=(
                        "The loaded model does not expose the classes this detector requires"
                    ),
                    requirement=specification["requirement"],
                    configured=configured,
                    required_classes=flat_required,
                    available_model_classes=model_classes,
                )
            )
            continue

        capabilities.append(
            DetectorCapability(
                detector_key=detector_key,
                implementation_state="IMPLEMENTED",
                availability_state="READY",
                reason=(
                    "Model exposes the required classes; a detector configuration is still required "
                    "before events are produced"
                    if not configured
                    else "Model and detector configuration are both present"
                ),
                requirement=specification["requirement"],
                configured=configured,
                required_classes=flat_required,
                available_model_classes=model_classes,
            )
        )
    return capabilities


def _configured_value(
    configs: Sequence[DetectorConfig],
    thresholds: Sequence[OperatingThreshold],
    detector_key: str,
    parameter: str,
    default: Any,
) -> tuple[Any, str, Optional[str]]:
    """Read a parameter from configuration, returning the value and its provenance."""
    for config in configs:
        if config.detector_key != detector_key or not config.is_enabled:
            continue
        parameters = config.parameters_json or {}
        if parameter in parameters:
            source_reference = config.source_reference
            source = config.threshold_source
            if source == "CONFIGURED" and not source_reference:
                source = ENGINEERING_DEFAULT
            return parameters[parameter], source, source_reference
    for threshold in thresholds:
        # A threshold row carries no enable flag: existing means in force. Its
        # provenance (threshold_source / source_reference) is returned with the
        # value so the caller can never present it as an IGL-validated rule.
        if threshold.code == f"{detector_key}.{parameter}":
            return threshold.value, threshold.threshold_source, threshold.source_reference
    return default, ENGINEERING_DEFAULT, None


def _foot_point(bbox: tuple[float, float, float, float], frame_width: int, frame_height: int) -> Optional[tuple[float, float]]:
    """Bottom-centre of a bounding box in normalized coordinates.

    The ground contact point is used for zone membership because that is where a
    person actually is. Returns None when the frame geometry is unknown, since a
    normalized point cannot be computed from an unknown frame size.
    """
    if frame_width <= 0 or frame_height <= 0:
        return None
    x1, y1, x2, y2 = bbox
    center_x = (x1 + x2) / 2.0
    bottom_y = y2
    return (
        min(max(center_x / frame_width, 0.0), 1.0),
        min(max(bottom_y / frame_height, 0.0), 1.0),
    )


def evaluate_restricted_zone(
    *,
    camera: Camera,
    zone: Optional[Zone],
    tracked_object,
    frame_width: int,
    frame_height: int,
) -> SafetyObservation:
    """Zone intrusion using configured polygon geometry and a real track."""
    if zone is None or zone.is_active is False:
        return SafetyObservation(
            "RESTRICTED_ZONE", False, "NOT_ASSESSABLE", None,
            "Camera is not attached to a zone", threshold_source=ENGINEERING_DEFAULT,
        )
    point = _foot_point(tracked_object.bbox, frame_width, frame_height)
    membership = evaluate_zone_membership(zone.id, zone.geometry_json, point)
    if membership.state == "NOT_CONFIGURED":
        return SafetyObservation(
            "RESTRICTED_ZONE", False, "NOT_ASSESSABLE", None,
            f"ZONE_GEOMETRY_NOT_CONFIGURED: {membership.reason}",
            zone_id=zone.id, threshold_source=ENGINEERING_DEFAULT,
        )
    if membership.state != "ASSESSED" or membership.inside is None:
        return SafetyObservation(
            "RESTRICTED_ZONE", False, "NOT_ASSESSABLE", None,
            f"ZONE_NOT_ASSESSABLE: {membership.reason}",
            zone_id=zone.id, threshold_source=ENGINEERING_DEFAULT,
        )
    inside = bool(membership.inside)
    return SafetyObservation(
        "RESTRICTED_ZONE",
        inside,
        "POSSIBLE" if inside else "NOT_ASSESSABLE",
        tracked_object.confidence,
        (
            f"Track {tracked_object.track_id} is inside configured zone {zone.code}"
            if inside
            else f"Track {tracked_object.track_id} is outside configured zone {zone.code}"
        ),
        subject_id=tracked_object.track_id,
        track_id=tracked_object.track_id,
        bbox=tuple(tracked_object.bbox),
        zone_id=zone.id,
        object_class=tracked_object.class_name,
        threshold_source=ENGINEERING_DEFAULT,
        extra={"zone_code": zone.code, "zone_type": zone.zone_type},
    )


def evaluate_frame_class_detector(
    detector_key: str,
    *,
    detections: Sequence[Any],
    model_classes: Sequence[str],
    minimum_confidence: float,
    threshold_source: str,
    source_reference: Optional[str],
) -> SafetyObservation:
    """Presence detectors (fire, smoke) over one real frame."""
    specification = DETECTOR_CATALOGUE.get(detector_key)
    if specification is None:
        return SafetyObservation(
            detector_key, False, "NOT_ASSESSABLE", None,
            f"Unknown detector key {detector_key}",
            threshold_source=threshold_source, extra={"source_reference": source_reference},
        )

    # Candidate class names come from the detector's own catalogue entry, so a
    # detector can never match a class it was not defined for.
    required_groups = specification["required_class_groups"]
    matched = [
        detection
        for detection in detections
        if detection.confidence is not None and detection.confidence >= minimum_confidence
    ]
    normalized_detections = {
        _normalize_class(detection.class_name): detection for detection in matched
    }
    hits = [
        detection
        for normalized_name, detection in normalized_detections.items()
        if any(
            _normalize_class(candidate) in normalized_name or normalized_name in _normalize_class(candidate)
            for group in required_groups
            for candidate in group
        )
    ]
    if not hits:
        return SafetyObservation(
            detector_key, False, "NOT_ASSESSABLE", None,
            f"No {detector_key.lower()} detection at or above the configured confidence floor",
            threshold_source=threshold_source, extra={"source_reference": source_reference},
        )
    best = max(hits, key=lambda detection: detection.confidence)
    return SafetyObservation(
        detector_key,
        True,
        "POSSIBLE",
        best.confidence,
        f"{detector_key} class {best.class_name} detected at confidence {best.confidence}",
        object_class=best.class_name,
        bbox=tuple(best.bbox),
        threshold_source=threshold_source,
        extra={"source_reference": source_reference, "class_name": best.class_name},
    )


class TemporalVerificationRegistry:
    """Holds one temporal verifier per subject, rebuilt when configuration changes.

    The verifier is stateful, so it lives in memory for the life of the process
    and is keyed by camera, detector and subject. It is intentionally not
    persisted: an observation window that survives a restart would be asserting
    continuity the process cannot prove.
    """

    def __init__(self) -> None:
        self._verifiers: Dict[str, TemporalVerifier] = {}
        self._policies: Dict[str, VerificationPolicy] = {}
        self._lock = RLock()

    def verifier_for(
        self,
        key: str,
        policy: VerificationPolicy,
    ) -> TemporalVerifier:
        with self._lock:
            existing_policy = self._policies.get(key)
            if existing_policy != policy or key not in self._verifiers:
                verifier = TemporalVerifier(policy)
                self._verifiers[key] = verifier
                self._policies[key] = policy
                return verifier
            return self._verifiers[key]

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._verifiers.clear()
                self._policies.clear()
                return
            self._verifiers.pop(key, None)
            self._policies.pop(key, None)


temporal_registry = TemporalVerificationRegistry()


def verification_policy_from_configuration(
    detector_key: str,
    configs: Sequence[DetectorConfig],
    thresholds: Sequence[OperatingThreshold],
) -> VerificationPolicy:
    """Build a temporal policy from configuration, never from a hard-coded verdict."""
    minimum_observations, observation_source, observation_reference = _configured_value(
        configs, thresholds, detector_key, "minimum_observations",
        settings.EVENT_MINIMUM_OBSERVATIONS,
    )
    minimum_duration, duration_source, duration_reference = _configured_value(
        configs, thresholds, detector_key, "minimum_duration_seconds",
        settings.EVENT_MINIMUM_DURATION_SECONDS,
    )
    maximum_gap, gap_source, gap_reference = _configured_value(
        configs, thresholds, detector_key, "maximum_gap_seconds",
        settings.EVENT_MAXIMUM_OBSERVATION_GAP_SECONDS,
    )
    confidence_floor, confidence_source, confidence_reference = _configured_value(
        configs, thresholds, detector_key, "minimum_confidence",
        settings.MODEL_CONFIDENCE_THRESHOLD,
    )
    confidence_source_value, _ = _source_rank(
        (observation_source, duration_source, gap_source, confidence_source)
    )
    references = [
        reference
        for reference in (observation_reference, duration_reference, gap_reference, confidence_reference)
        if reference
    ]
    return VerificationPolicy(
        minimum_observations=int(minimum_observations),
        minimum_duration_seconds=float(minimum_duration),
        confidence_threshold=float(confidence_floor),
        maximum_gap_seconds=float(maximum_gap),
        threshold_source="CONFIGURED" if confidence_source_value == "CONFIGURED" and references else ENGINEERING_DEFAULT,
        rule_reference=references[0] if references else None,
    )


def _source_rank(sources: Sequence[str]) -> tuple[str, Optional[str]]:
    """Weakest provenance wins: one engineering default makes the policy a default."""
    if all(source == "CONFIGURED" for source in sources):
        return "CONFIGURED", None
    return ENGINEERING_DEFAULT, None


def event_type_for(detector_key: str) -> str:
    return {
        "RESTRICTED_ZONE": "RESTRICTED_ZONE_INTRUSION",
        "FIRE": "FIRE",
        "SMOKE": "SMOKE",
    }.get(detector_key, detector_key)


def default_severity(detector_key: str, severity: Optional[str] = None) -> str:
    if severity:
        return severity
    return {"FIRE": "CRITICAL", "SMOKE": "HIGH", "RESTRICTED_ZONE": "HIGH"}.get(detector_key, "MEDIUM")