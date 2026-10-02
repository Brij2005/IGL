"""Safety condition evaluation over real model output.

This module is the only place where a safety event can be created from a
detection. Its contract is deliberately narrow:

* A detector may only be evaluated when a real model produced the underlying
  detection. No configuration, no threshold and no operator action can create an
  event by itself.
* Every detector declares what it needs. A detector whose requirements are not
  met reports an explicit unsupported state (``NOT_IMPLEMENTED``,
  ``MODEL_NOT_CONFIGURED``, ``MODEL_CLASS_NOT_AVAILABLE``,
  ``NOT_AVAILABLE``) and evaluates nothing.
* A class-based detector (``PERSON``, ``PHONE``) is only operational when the
  loaded model itself exposes that class in its own class list. This repository
  ships no weights file, so no such detector can be exercised here; without a
  class they report an explicit unsupported state and are never described as
  running.
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

# Availability vocabulary for a detector this build implements. These are states,
# not booleans, so an unsupported detector can never be read as merely idle.
MODEL_CLASS_NOT_AVAILABLE = "MODEL_CLASS_NOT_AVAILABLE"
NOT_AVAILABLE = "NOT_AVAILABLE"

# Explicit detector catalogue. ``required_class_groups`` is matched against the
# loaded model's own class list; a detector whose classes the model does not
# contain is unsupported by that model rather than silently producing nothing.
# ``detection`` states what the detector can physically decide, and is NONE for
# every detector this build does not implement.
DETECTOR_CATALOGUE: Dict[str, Dict[str, Any]] = {
    "RESTRICTED_ZONE": {
        "description": "Person or vehicle inside a configured restricted zone polygon",
        "requirement": "A person/vehicle class plus a zone polygon configured in normalized frame coordinates",
        "required_class_groups": (("person", "worker", "human"), ("forklift", "vehicle", "truck")),
        "any_group_sufficient": True,
        "subject": "TRACK",
        "state": "IMPLEMENTED",
        "detection": "TRACKED_OBJECT_IN_CONFIGURED_ZONE",
    },
    "FIRE": {
        "description": "Fire class detected by the configured model",
        "requirement": "A fire/flame class present in the loaded model's classes",
        "required_class_groups": (("fire", "flame"),),
        "any_group_sufficient": True,
        "subject": "FRAME",
        "state": "IMPLEMENTED",
        "detection": "CLASS_PRESENCE_IN_FRAME",
    },
    "SMOKE": {
        "description": "Smoke class detected by the configured model",
        "requirement": "A smoke class present in the loaded model's classes",
        "required_class_groups": (("smoke", "fumes"),),
        "any_group_sufficient": True,
        "subject": "FRAME",
        "state": "IMPLEMENTED",
        "detection": "CLASS_PRESENCE_IN_FRAME",
    },
    "PERSON": {
        "description": "A person class present in the camera view",
        "requirement": "A person class present in the loaded model's classes",
        "required_class_groups": (("person", "worker", "human"),),
        "any_group_sufficient": True,
        "subject": "FRAME",
        "state": "IMPLEMENTED",
        "detection": "CLASS_PRESENCE_IN_FRAME",
    },
    "PHONE": {
        "description": "A mobile or cell phone class object present in the camera view",
        "requirement": "A cell phone class present in the loaded model's classes",
        "required_class_groups": (("cell phone", "phone", "mobile phone", "cellphone"),),
        # A substring match on "phone" would otherwise fire on a wearable audio
        # device or a fixed-line handset, which are different objects from the
        # mobile phone this rule is about. Only an exact label match excludes.
        "excluded_class_groups": (("headphone", "headphones", "headset", "earphone"), ("telephone", "landline")),
        "any_group_sufficient": True,
        "subject": "FRAME",
        "state": "IMPLEMENTED",
        "detection": "CLASS_PRESENCE_IN_FRAME",
    },
    "PPE": {
        "description": "Personal protective equipment compliance",
        "requirement": "A model that can assess both presence and absence of the item",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "detection": "NONE",
        "reason": "PPE compliance is an absence claim. This build has no detector that can establish that an item is missing, so no compliance verdict is produced.",
    },
    "HELMET": {
        "description": "Hard hat compliance",
        "requirement": "A model that can assess both presence and absence of a hard hat",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "detection": "NONE",
        "reason": "Hard hat compliance is an absence claim and is not implemented in this build.",
    },
    "SAFETY_VEST": {
        "description": "High-visibility vest compliance",
        "requirement": "A model that can assess both presence and absence of a vest",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "detection": "NONE",
        "reason": "Vest compliance is an absence claim and is not implemented in this build.",
    },
    "PROXIMITY": {
        "description": "Proximity between a person and equipment or a hazard",
        "requirement": "A calibrated camera-space scale in metres per pixel",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "detection": "NONE",
        "reason": "Proximity needs a calibrated pixel-to-metre scale, which this deployment does not supply.",
    },
    "FALL": {
        "description": "Person fall detection",
        "requirement": "A pose model and a trained fall classifier",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "detection": "NONE",
        "reason": "No pose or fall classifier is configured.",
    },
    "LEAKAGE": {
        "description": "Fluid or gas leakage detection",
        "requirement": "A validated leakage detection model",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "detection": "NONE",
        "reason": "No leakage detection model is configured.",
    },
    "UNSAFE_BEHAVIOR": {
        "description": "Unsafe behaviour classification",
        "requirement": "A validated behaviour classification model",
        "required_class_groups": (),
        "any_group_sufficient": False,
        "subject": "NONE",
        "state": "NOT_IMPLEMENTED",
        "detection": "NONE",
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


def _class_is_excluded(class_name: str, excluded_groups: Sequence[Sequence[str]]) -> bool:
    """Exact-label exclusion only.

    Exclusions are matched on the whole normalized label so a substring guard
    cannot discard a legitimate class: "cellphone" is a phone, and excluding it
    because it contains "telephone" would be wrong.
    """
    normalized = _normalize_class(class_name)
    return any(normalized == _normalize_class(candidate) for group in excluded_groups for candidate in group)


def matched_class_candidate(detector_key: str, class_name: str) -> Optional[str]:
    """Return the catalogue label a detection class matches, or None.

    The match is a heuristic over the model's own labels: an exact normalized
    label match is preferred, and a containment match is only accepted when no
    catalogue label is an exact match. The matched catalogue label is returned so
    the observation reason can name it, so an operator can see which rule fired.
    """
    specification = DETECTOR_CATALOGUE.get(detector_key)
    if specification is None:
        return None
    if _class_is_excluded(class_name, specification.get("excluded_class_groups") or ()):
        return None
    normalized = _normalize_class(class_name)
    candidates = [
        candidate
        for group in specification["required_class_groups"]
        for candidate in group
    ]
    for candidate in candidates:
        if _normalize_class(candidate) == normalized:
            return candidate
    for candidate in candidates:
        candidate_normalized = _normalize_class(candidate)
        if candidate_normalized in normalized or normalized in candidate_normalized:
            return candidate
    return None


# Every per-detector parameter an operator may set, in the order it is reported.
RULE_PARAMETER_NAMES = (
    "minimum_confidence",
    "minimum_observations",
    "minimum_duration_seconds",
    "maximum_gap_seconds",
    "debounce_seconds",
    "cooldown_seconds",
    "severity",
    "zone_scoping",
    "evidence_policy",
    "correlation_window_seconds",
)

# An operator may name the confidence floor either way. Both keys resolve to the
# same parameter, in both DetectorConfig.parameters_json and the matching
# ``<DETECTOR>.<name>`` OperatingThreshold code, so a configuration written
# either way is honoured instead of silently ignored.
RULE_PARAMETER_ALIASES: Dict[str, str] = {
    "confidence_threshold": "minimum_confidence",
    "confidence_floor": "minimum_confidence",
}

# The only evidence policies this build implements. Anything else is reported as
# unrecognised and falls back to CAPTURE, so a typo cannot silently stop the
# evidence a real event needs.
EVIDENCE_POLICIES = ("CAPTURE", "SKIP")
ZONE_SCOPING_ALL = "ALL"


@dataclass(frozen=True)
class DetectorRuleParameters:
    """The effective per-detector rule, with the provenance of every value.

    Every value here is either operator-configured or an engineering default
    pending IGL validation. The two are never mixed silently: ``sources`` reports
    which is which for each individual parameter.
    """

    detector_key: str
    minimum_confidence: float
    minimum_observations: int
    minimum_duration_seconds: float
    maximum_gap_seconds: float
    debounce_seconds: float
    cooldown_seconds: float
    severity: str
    zone_scoping: str
    evidence_policy: str
    correlation_window_seconds: int
    sources: Dict[str, str] = field(default_factory=dict)
    references: Dict[str, Optional[str]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def source_for(self, parameter: str) -> str:
        return self.sources.get(parameter, ENGINEERING_DEFAULT)

    def excludes_zone(self, zone_id: Optional[str]) -> bool:
        """True when this rule is scoped to a zone the observation is not in."""
        scope = (self.zone_scoping or ZONE_SCOPING_ALL).strip()
        if scope.upper() == ZONE_SCOPING_ALL:
            return False
        return zone_id is None or str(zone_id) != scope

    def as_dict(self) -> Dict[str, Any]:
        return {
            "minimum_confidence": self.minimum_confidence,
            "minimum_observations": self.minimum_observations,
            "minimum_duration_seconds": self.minimum_duration_seconds,
            "maximum_gap_seconds": self.maximum_gap_seconds,
            "debounce_seconds": self.debounce_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "severity": self.severity,
            "zone_scoping": self.zone_scoping,
            "evidence_policy": self.evidence_policy,
            "correlation_window_seconds": self.correlation_window_seconds,
            "sources": dict(self.sources),
            "source_references": {name: value for name, value in self.references.items() if value},
            "notes": list(self.notes),
        }


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
    enabled: bool = False
    detection_capability: str = "NONE"
    validation_state: str = "NOT_CONFIGURED"
    configuration: Dict[str, Any] = field(default_factory=dict)
    health: Dict[str, Any] = field(default_factory=dict)

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
            "enabled": self.enabled,
            "required_classes": list(self.required_classes),
            "available_model_classes": list(self.available_model_classes),
            "detection_capability": self.detection_capability,
            "validation_state": self.validation_state,
            "configuration": self.configuration,
            "health": self.health,
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
    thresholds: Optional[Sequence[OperatingThreshold]] = None,
) -> List[DetectorCapability]:
    """Report what each detector can actually do right now.

    The result distinguishes "this build does not implement it", "no model is
    configured", "this model cannot support it", "nothing is configured yet" and
    "ready", so a frontend cannot present an unsupported detector as active. The
    effective rule configuration, the model health the decision was made from,
    and the operator validation state are reported alongside, so what the
    platform decided and why are both visible.
    """
    model_classes = tuple(model_health.get("classes") or ())
    model_status = str(model_health.get("status") or "MODEL_NOT_CONFIGURED")
    model_available = bool(model_health.get("available"))
    all_configs = list(configs or ())
    all_thresholds = list(thresholds or ())
    enabled_configs = [config for config in all_configs if config.is_enabled]
    configured_keys = {config.detector_key for config in enabled_configs}

    capabilities: List[DetectorCapability] = []
    for detector_key, specification in DETECTOR_CATALOGUE.items():
        required_groups = specification["required_class_groups"]
        flat_required = tuple(group[0] for group in required_groups)
        configured = detector_key in configured_keys
        matching_config = next(
            (config for config in enabled_configs if config.detector_key == detector_key), None
        )
        rule = detector_rule_parameters(
            detector_key,
            all_configs,
            all_thresholds,
            default_confidence=model_health.get("confidence_threshold"),
        )
        configuration: Dict[str, Any] = {
            "config_id": matching_config.id if matching_config is not None else None,
            "scope_camera_id": matching_config.camera_id if matching_config is not None else None,
            "scope_zone_id": matching_config.zone_id if matching_config is not None else None,
            "threshold_source": (
                matching_config.threshold_source
                if matching_config is not None
                else ENGINEERING_DEFAULT
            ),
            "source_reference": matching_config.source_reference if matching_config is not None else None,
            "effective_rule": rule.as_dict(),
        }
        health: Dict[str, Any] = {
            "model_status": model_status,
            "model_available": model_available,
            "model_name": model_health.get("model_name"),
            "model_version": model_health.get("model_version"),
            "weights_checksum_sha256": model_health.get("weights_checksum_sha256"),
            "model_class_count": len(model_classes),
            # No detector in this build has been validated against real IGL
            # footage, so the validation state is never claimed as VALIDATED.
            "igl_validation_status": "NOT_VALIDATED",
        }

        if specification["state"] != "IMPLEMENTED":
            health["required_classes_present"] = None
            capabilities.append(
                DetectorCapability(
                    detector_key=detector_key,
                    implementation_state="NOT_IMPLEMENTED",
                    availability_state=NOT_AVAILABLE,
                    reason=specification["reason"],
                    requirement=specification["requirement"],
                    configured=configured,
                    required_classes=flat_required,
                    available_model_classes=model_classes,
                    enabled=configured,
                    detection_capability=specification.get("detection", "NONE"),
                    validation_state=(
                        matching_config.validation_status if matching_config is not None else "NOT_CONFIGURED"
                    ),
                    configuration=configuration,
                    health=health,
                )
            )
            continue

        if not model_available:
            health["required_classes_present"] = None
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
                    enabled=configured,
                    detection_capability=specification.get("detection", "NONE"),
                    validation_state=(
                        matching_config.validation_status if matching_config is not None else "NOT_CONFIGURED"
                    ),
                    configuration=configuration,
                    health=health,
                )
            )
            continue

        supported = (
            _any_class_available(model_classes, required_groups)
            if specification["any_group_sufficient"]
            else _classes_available(model_classes, required_groups)
        )
        health["required_classes_present"] = supported
        if not supported:
            capabilities.append(
                DetectorCapability(
                    detector_key=detector_key,
                    implementation_state="IMPLEMENTED",
                    availability_state=MODEL_CLASS_NOT_AVAILABLE,
                    reason=(
                        "The loaded model does not expose the classes this detector requires, "
                        f"so this detector is implemented but unsupported by the loaded model "
                        f"(it exposes {len(model_classes)} classes)"
                    ),
                    requirement=specification["requirement"],
                    configured=configured,
                    required_classes=flat_required,
                    available_model_classes=model_classes,
                    enabled=configured,
                    detection_capability=specification.get("detection", "NONE"),
                    validation_state=(
                        matching_config.validation_status if matching_config is not None else "NOT_CONFIGURED"
                    ),
                    configuration=configuration,
                    health=health,
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
                enabled=configured,
                detection_capability=specification.get("detection", "NONE"),
                validation_state=(
                    matching_config.validation_status if matching_config is not None else "NOT_CONFIGURED"
                ),
                configuration=configuration,
                health=health,
            )
        )
    return capabilities



def _accepted_parameter_names(parameter: str) -> tuple[str, ...]:
    """The canonical name plus every alias that resolves to it."""
    aliases = tuple(name for name, target in RULE_PARAMETER_ALIASES.items() if target == parameter)
    return (parameter, *aliases)


def _configured_value(
    configs: Sequence[DetectorConfig],
    thresholds: Sequence[OperatingThreshold],
    detector_key: str,
    parameter: str,
    default: Any,
) -> tuple[Any, str, Optional[str]]:
    """Read a parameter from configuration, returning the value and its provenance."""
    accepted = _accepted_parameter_names(parameter)
    for config in configs:
        if config.detector_key != detector_key or not config.is_enabled:
            continue
        parameters = config.parameters_json or {}
        for name in accepted:
            if name in parameters:
                source_reference = config.source_reference
                source = config.threshold_source
                if source == "CONFIGURED" and not source_reference:
                    source = ENGINEERING_DEFAULT
                return parameters[name], source, source_reference
    for threshold in thresholds:
        # A threshold row carries no enable flag: existing means in force. Its
        # provenance (threshold_source / source_reference) is returned with the
        # value so the caller can never present it as an IGL-validated rule.
        for name in accepted:
            if threshold.code == f"{detector_key}.{name}":
                return threshold.value, threshold.threshold_source, threshold.source_reference
    return default, ENGINEERING_DEFAULT, None


def _validated_float(
    raw: Any,
    *,
    default: float,
    minimum: float,
    maximum: float,
    detector_key: str,
    parameter: str,
    notes: List[str],
) -> float:
    """Coerce a configured number, refusing to fail a live frame on bad input.

    A value that is not a number falls back to the engineering default, never to
    a permissive zero: a typo in a confidence floor must not turn the floor off.
    An out-of-range value is clamped and reported. Either way the correction is
    recorded in ``notes`` so the configuration can be fixed, rather than being
    dropped silently while every other detector still reports for the frame.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        notes.append(
            f"{parameter}: configured value {raw!r} is not a number; the engineering default is in force"
        )
        return float(default)
    if value != value:  # NaN
        notes.append(f"{parameter}: configured value is not a number; the engineering default is in force")
        return float(default)
    clamped = min(max(value, minimum), maximum)
    if clamped != value:
        notes.append(
            f"{parameter}: configured value {value} is outside {minimum}..{maximum}; clamped to {clamped}"
        )
    return clamped


def detector_rule_parameters(
    detector_key: str,
    configs: Sequence[DetectorConfig],
    thresholds: Sequence[OperatingThreshold],
    *,
    default_confidence: Optional[float] = None,
) -> DetectorRuleParameters:
    """Resolve the effective rule for one detector from configuration.

    Operator configuration wins over the ``<DETECTOR>.<name>`` OperatingThreshold
    rows, which win over the engineering defaults in settings. Whatever is used,
    the provenance travels with the value so an unvalidated default can never be
    presented as an IGL operating value.
    """
    notes: List[str] = []
    sources: Dict[str, str] = {}
    references: Dict[str, Optional[str]] = {}
    resolved: Dict[str, Any] = {}

    def read(parameter: str, default: Any) -> Any:
        value, source, reference = _configured_value(
            configs, thresholds, detector_key, parameter, default
        )
        sources[parameter] = source
        references[parameter] = reference
        resolved[parameter] = value
        return value

    confidence_default = (
        float(default_confidence)
        if default_confidence is not None
        else float(settings.MODEL_CONFIDENCE_THRESHOLD)
    )
    minimum_confidence = _validated_float(
        read("minimum_confidence", confidence_default),
        default=confidence_default,
        minimum=0.0,
        maximum=1.0,
        detector_key=detector_key,
        parameter="minimum_confidence",
        notes=notes,
    )
    observation_default = settings.EVENT_MINIMUM_OBSERVATIONS
    minimum_observations = max(1, int(float(read("minimum_observations", observation_default))))
    duration_default = float(settings.EVENT_MINIMUM_DURATION_SECONDS)
    minimum_duration = _validated_float(
        read("minimum_duration_seconds", duration_default),
        default=duration_default,
        minimum=0.0,
        maximum=3600.0,
        detector_key=detector_key,
        parameter="minimum_duration_seconds",
        notes=notes,
    )
    gap_default = float(settings.EVENT_MAXIMUM_OBSERVATION_GAP_SECONDS)
    maximum_gap = _validated_float(
        read("maximum_gap_seconds", gap_default),
        default=gap_default,
        minimum=0.0,
        maximum=3600.0,
        detector_key=detector_key,
        parameter="maximum_gap_seconds",
        notes=notes,
    )
    debounce_default = float(settings.SAFETY_RULE_DEBOUNCE_SECONDS)
    debounce = _validated_float(
        read("debounce_seconds", debounce_default),
        default=debounce_default,
        minimum=0.0,
        maximum=3600.0,
        detector_key=detector_key,
        parameter="debounce_seconds",
        notes=notes,
    )
    cooldown_default = float(settings.SAFETY_RULE_COOLDOWN_SECONDS)
    cooldown = _validated_float(
        read("cooldown_seconds", cooldown_default),
        default=cooldown_default,
        minimum=0.0,
        maximum=86400.0,
        detector_key=detector_key,
        parameter="cooldown_seconds",
        notes=notes,
    )
    correlation_window = max(
        1, int(float(read("correlation_window_seconds", settings.EVENT_CORRELATION_WINDOW_SECONDS)))
    )

    severity = str(read("severity", default_severity(detector_key))).strip().upper() or "MEDIUM"
    zone_scoping = str(read("zone_scoping", ZONE_SCOPING_ALL)).strip() or ZONE_SCOPING_ALL

    evidence_policy = str(read("evidence_policy", EVIDENCE_POLICIES[0])).strip().upper()
    if evidence_policy not in EVIDENCE_POLICIES:
        notes.append(
            f"evidence_policy: {evidence_policy!r} is not one of {list(EVIDENCE_POLICIES)}; "
            f"{EVIDENCE_POLICIES[0]} is in force"
        )
        evidence_policy = EVIDENCE_POLICIES[0]
        sources["evidence_policy"] = ENGINEERING_DEFAULT
        references["evidence_policy"] = None

    return DetectorRuleParameters(
        detector_key=detector_key,
        minimum_confidence=minimum_confidence,
        minimum_observations=minimum_observations,
        minimum_duration_seconds=minimum_duration,
        maximum_gap_seconds=maximum_gap,
        debounce_seconds=debounce,
        cooldown_seconds=cooldown,
        severity=severity,
        zone_scoping=zone_scoping,
        evidence_policy=evidence_policy,
        correlation_window_seconds=correlation_window,
        sources=sources,
        references=references,
        notes=tuple(notes),
    )


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
    """Presence detectors (fire, smoke, person, phone) over one real frame."""
    specification = DETECTOR_CATALOGUE.get(detector_key)
    if specification is None:
        return SafetyObservation(
            detector_key, False, "NOT_ASSESSABLE", None,
            f"Unknown detector key {detector_key}",
            threshold_source=threshold_source, extra={"source_reference": source_reference},
        )
    if specification["state"] != "IMPLEMENTED":
        return SafetyObservation(
            detector_key, False, "NOT_ASSESSABLE", None,
            f"NOT_IMPLEMENTED: {specification.get('reason')}",
            threshold_source=threshold_source, extra={"source_reference": source_reference},
        )

    # Candidate class names come from the detector's own catalogue entry, so a
    # detector can never match a class it was not defined for. The label the
    # detector matched is carried on the observation so the reason a rule fired
    # is always visible.
    matched = [
        detection
        for detection in detections
        if detection.confidence is not None and detection.confidence >= minimum_confidence
    ]
    hits = []
    for detection in matched:
        candidate = matched_class_candidate(detector_key, detection.class_name)
        if candidate is not None:
            hits.append((candidate, detection))
    if not hits:
        return SafetyObservation(
            detector_key, False, "NOT_ASSESSABLE", None,
            f"No {detector_key.lower()} detection at or above the configured confidence floor",
            threshold_source=threshold_source, extra={"source_reference": source_reference},
        )
    matched_label, best = max(hits, key=lambda item: item[1].confidence)
    return SafetyObservation(
        detector_key,
        True,
        "POSSIBLE",
        best.confidence,
        (
            f"{detector_key} class {best.class_name} matched rule label '{matched_label}' "
            f"at confidence {best.confidence}"
        ),
        object_class=best.class_name,
        bbox=tuple(best.bbox),
        threshold_source=threshold_source,
        extra={
            "source_reference": source_reference,
            "class_name": best.class_name,
            "matched_rule_class": matched_label,
        },
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
        "PERSON": "PERSON_DETECTED",
        "PHONE": "MOBILE_PHONE_DETECTED",
    }.get(detector_key, detector_key)


def default_severity(detector_key: str, severity: Optional[str] = None) -> str:
    """Severity for a new event.

    A per-rule configured severity wins; otherwise the per-detector default
    applies. The per-detector defaults are engineering defaults pending IGL
    validation, not agreed severity classification for any IGL area.
    """
    if severity:
        return severity
    return {"FIRE": "CRITICAL", "SMOKE": "HIGH", "RESTRICTED_ZONE": "HIGH"}.get(detector_key, "MEDIUM")
