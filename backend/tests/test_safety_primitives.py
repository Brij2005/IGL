from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.temporal_verifier import TemporalVerifier, VerificationPolicy
from app.services.ppe_rules import assess_ppe_observation
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
