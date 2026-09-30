"""PPE rule interpretation only; this module does not perform model inference."""
from dataclasses import dataclass
from typing import Literal


PPEFinding = Literal["PPE_PRESENT", "PPE_MISSING", "NOT_ASSESSABLE", "NOT_VALIDATED"]


@dataclass(frozen=True)
class PPEAssessment:
    ppe_type: str
    finding: PPEFinding
    observation_state: str
    reason: str | None = None


def assess_ppe_observation(
    ppe_type: str,
    *,
    present: bool | None,
    confidence: float | None,
    minimum_confidence: float,
    model_configured: bool,
    model_validated_for_task: bool,
    region_visible: bool,
) -> PPEAssessment:
    if not ppe_type.strip():
        raise ValueError("ppe_type is required from authorized configuration")
    if not 0.0 <= minimum_confidence <= 1.0:
        raise ValueError("minimum_confidence must be between 0 and 1")
    if not model_configured or not model_validated_for_task:
        return PPEAssessment(ppe_type, "NOT_VALIDATED", "NOT_VALIDATED", "Suitable validated PPE model is unavailable")
    if not region_visible or present is None:
        return PPEAssessment(ppe_type, "NOT_ASSESSABLE", "NOT_ASSESSABLE", "Required PPE region is not assessable")
    if confidence is None or not 0.0 <= confidence <= 1.0:
        return PPEAssessment(ppe_type, "NOT_ASSESSABLE", "NOT_ASSESSABLE", "Confidence evidence is unavailable")
    if confidence < minimum_confidence:
        return PPEAssessment(ppe_type, "NOT_ASSESSABLE", "POSSIBLE", "Confidence is below configured rule threshold")
    return PPEAssessment(ppe_type, "PPE_PRESENT" if present else "PPE_MISSING", "POSSIBLE", "Requires temporal verification before any event")