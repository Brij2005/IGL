from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.engine.temporal_verifier import TemporalVerifier, VerificationPolicy
from app.services.ppe_rules import assess_ppe_observation
from app.services.safety_engine import RULE_PARAMETER_NAMES, detector_rule_parameters
from app.services.zone_engine import evaluate_zone_membership, point_in_polygon, validate_polygon
from app.schemas_configuration import OperatingThresholdCreate, PPERuleCreate, SafetyRuleCreate, ZoneCreate


def test_temporal_verifier_requires_persistence_and_duration():
    verifier = TemporalVerifier(VerificationPolicy(
        minimum_observations=3,
        minimum_duration_seconds=2,
        confidence_threshold=0.7,
        maximum_gap_seconds=1.5,
        threshold_source="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION",
    ))
    start = datetime.now(timezone.utc)
    assert verifier.observe("test-track", start, True, 0.9).state == "DETECTED"
    assert verifier.observe("test-track", start + timedelta(seconds=1), True, 0.9).state == "PERSISTED"
    result = verifier.observe("test-track", start + timedelta(seconds=2), True, 0.9)
    assert result.state == "VERIFIED"
    assert result.threshold_source == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"


def test_temporal_verifier_invalidates_gap_and_marks_missing_evidence():
    verifier = TemporalVerifier(VerificationPolicy(2, 0, 0.5, 1, "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"))
    start = datetime.now(timezone.utc)
    verifier.observe("track", start, True, 0.8)
    assert verifier.observe("track", start + timedelta(seconds=2), True, 0.8).state == "DETECTED"
    assert verifier.observe("track", start + timedelta(seconds=3), False, 1.0).state == "INVALIDATED"
    assert verifier.observe("track", start + timedelta(seconds=4), None, None).state == "NOT_ASSESSABLE"


def test_configured_temporal_rule_requires_a_provenance_reference():
    with pytest.raises(ValueError, match="source reference"):
        VerificationPolicy(2, 1, 0.5, 1, "CONFIGURED")


def test_zone_membership_uses_supplied_polygon_only():
    polygon = ((0, 0), (1, 0), (1, 1), (0, 1))
    assert point_in_polygon((0.5, 0.5), polygon) is True
    assert point_in_polygon((1.5, 0.5), polygon) is False
    assert evaluate_zone_membership("test-zone", None, (0.5, 0.5)).state == "NOT_CONFIGURED"
    assert evaluate_zone_membership("test-zone", polygon, None).state == "NOT_ASSESSABLE"
    assert evaluate_zone_membership("test-zone", polygon, (0.5, 0.5)).inside is True
    assert point_in_polygon((5, 5), ((0, 0), (1, 1))) is None


def test_zone_polygon_rejects_out_of_bounds_and_self_intersection():
    assert validate_polygon([[0, 0], [1.1, 0], [1, 1]]) is None
    assert validate_polygon([[0, 0], [1, 1], [0, 1], [0.8, 0.3]]) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "RULE_1", "name": "Rule", "validation_status": "VALIDATED"},
        {
            "code": "RULE_2", "name": "Rule", "zone_id": "zone-1",
            "metric": "confidence", "value": 0.8, "threshold_source": "CONFIGURED",
            "source_reference": "OPERATOR-REFERENCE", "validation_status": "VALIDATED",
        },
    ],
)
def test_configuration_cannot_self_assert_validation(payload):
    model = SafetyRuleCreate if "code" in payload and "zone_id" not in payload else OperatingThresholdCreate
    with pytest.raises(ValueError):
        model.model_validate(payload)


def test_ppe_rule_requires_validated_model_and_visible_region():
    common = {
        "ppe_type": "TEST_PPE_CLASS",
        "present": True,
        "confidence": 0.99,
        "minimum_confidence": 0.5,
    }
    unvalidated = assess_ppe_observation(
        **common, region_visible=True, model_configured=True, model_validated_for_task=False
    )
    assert unvalidated.finding == "NOT_VALIDATED"
    not_visible = assess_ppe_observation(
        **common, region_visible=False, model_configured=True, model_validated_for_task=True
    )
    assert not_visible.finding == "NOT_ASSESSABLE"
    observed = assess_ppe_observation(
        **common, region_visible=True, model_configured=True, model_validated_for_task=True
    )
    assert observed.finding == "PPE_PRESENT"
    assert observed.observation_state == "POSSIBLE"


def test_zone_and_ppe_configuration_require_real_operator_inputs():
    zone = ZoneCreate(
        area_id="operator-supplied-area",
        name="Operator supplied zone",
        code="OP-ZONE-1",
        zone_type="WORK_AREA",
        geometry_json=[[0, 0], [1, 0], [1, 1]],
    )
    assert zone.geometry_json == [[0, 0], [1, 0], [1, 1]]
    with pytest.raises(ValueError, match="coordinate pairs"):
        ZoneCreate(
            area_id="operator-supplied-area",
            name="Invalid zone",
            code="OP-ZONE-2",
            zone_type="RESTRICTED",
            geometry_json=[[0, 0], [1, 1]],
        )
    default_rule = PPERuleCreate(zone_id="operator-zone", ppe_type="operator-configured-item", is_mandatory=True)
    assert default_rule.min_confidence is None
    assert default_rule.threshold_source == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
    with pytest.raises(ValueError, match="source reference"):
        PPERuleCreate(
            zone_id="operator-zone",
            ppe_type="operator-configured-item",
            is_mandatory=True,
            min_confidence=0.8,
            threshold_source="CONFIGURED",
        )


# ---------------------------------------------------------------------------
# Safety rule parameters, resolved without any database
# ---------------------------------------------------------------------------

class _StubConfig:
    """The attributes of a DetectorConfig row the rule resolver reads."""

    def __init__(self, detector_key, parameters, threshold_source="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION", source_reference=None, is_enabled=True):
        self.detector_key = detector_key
        self.parameters_json = parameters
        self.threshold_source = threshold_source
        self.source_reference = source_reference
        self.is_enabled = is_enabled


class _StubThreshold:
    """The attributes of an OperatingThreshold row the rule resolver reads."""

    def __init__(self, code, value, threshold_source="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION", source_reference=None):
        self.code = code
        self.value = value
        self.threshold_source = threshold_source
        self.source_reference = source_reference


def test_unconfigured_rule_reports_every_value_as_an_engineering_default():
    """Nothing here is an IGL operating value, and the rule says so per value."""
    rule = detector_rule_parameters("RESTRICTED_ZONE", [], [])
    assert rule.minimum_confidence == pytest.approx(settings.MODEL_CONFIDENCE_THRESHOLD)
    assert rule.minimum_observations == settings.EVENT_MINIMUM_OBSERVATIONS
    assert rule.minimum_duration_seconds == settings.EVENT_MINIMUM_DURATION_SECONDS
    assert rule.maximum_gap_seconds == settings.EVENT_MAXIMUM_OBSERVATION_GAP_SECONDS
    assert rule.debounce_seconds == settings.SAFETY_RULE_DEBOUNCE_SECONDS
    assert rule.cooldown_seconds == settings.SAFETY_RULE_COOLDOWN_SECONDS
    assert rule.correlation_window_seconds == settings.EVENT_CORRELATION_WINDOW_SECONDS
    assert rule.evidence_policy == "CAPTURE"
    assert rule.zone_scoping == "ALL"
    assert rule.severity == "HIGH"
    assert set(rule.sources) == set(RULE_PARAMETER_NAMES)
    assert set(rule.sources.values()) == {"ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"}
    assert all(reference is None for reference in rule.references.values())
    assert rule.notes == ()


def test_configured_rule_keeps_the_provenance_of_each_value_separately():
    configs = [
        _StubConfig(
            "FIRE",
            {"confidence_threshold": 0.6, "evidence_policy": "SKIP"},
            threshold_source="CONFIGURED",
            source_reference="IGL-SOP-FIRE-010",
        )
    ]
    thresholds = [_StubThreshold("FIRE.debounce_seconds", 45.0, "CONFIGURED", "IGL-SOP-FIRE-011")]
    rule = detector_rule_parameters("FIRE", configs, thresholds)
    # The alias and the OperatingThreshold code are both honoured.
    assert rule.minimum_confidence == pytest.approx(0.6)
    assert rule.evidence_policy == "SKIP"
    assert rule.debounce_seconds == pytest.approx(45.0)
    for parameter in ("minimum_confidence", "evidence_policy", "debounce_seconds"):
        assert rule.source_for(parameter) == "CONFIGURED"
    assert rule.references["minimum_confidence"] == "IGL-SOP-FIRE-010"
    assert rule.references["debounce_seconds"] == "IGL-SOP-FIRE-011"
    # A value nobody configured keeps its default provenance.
    assert rule.source_for("cooldown_seconds") == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
    assert rule.references["cooldown_seconds"] is None


def test_a_disabled_configuration_is_ignored_by_the_rule_resolver():
    """An operator who switched a rule off has not configured it."""
    configs = [_StubConfig("FIRE", {"debounce_seconds": 300.0}, is_enabled=False)]
    rule = detector_rule_parameters("FIRE", configs, [])
    assert rule.debounce_seconds == settings.SAFETY_RULE_DEBOUNCE_SECONDS
    assert rule.source_for("debounce_seconds") == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"


def test_a_configured_value_without_a_reference_is_downgraded_to_a_default():
    """The same provenance rule the temporal policy already follows."""
    configs = [_StubConfig("SMOKE", {"debounce_seconds": 30.0}, threshold_source="CONFIGURED")]
    rule = detector_rule_parameters("SMOKE", configs, [])
    assert rule.debounce_seconds == pytest.approx(30.0)
    assert rule.source_for("debounce_seconds") == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"


def test_unusable_configured_values_are_corrected_and_reported():
    configs = [
        _StubConfig(
            "PHONE",
            {"minimum_confidence": "not-a-number", "cooldown_seconds": -5.0, "evidence_policy": 17},
        )
    ]
    rule = detector_rule_parameters("PHONE", configs, [])
    assert rule.minimum_confidence == pytest.approx(settings.MODEL_CONFIDENCE_THRESHOLD)
    assert rule.cooldown_seconds == pytest.approx(0.0)
    assert rule.evidence_policy == "CAPTURE"
    assert len(rule.notes) == 3


def test_zone_scoping_only_excludes_a_zone_the_rule_does_not_name():
    assert detector_rule_parameters("FIRE", [], []).excludes_zone("any-zone") is False
    scoped = detector_rule_parameters("FIRE", [_StubConfig("FIRE", {"zone_scoping": "zone-42"})], [])
    assert scoped.excludes_zone("zone-42") is False
    assert scoped.excludes_zone("zone-7") is True
    # An observation with no zone at all cannot be claimed as being in scope.
    assert scoped.excludes_zone(None) is True
