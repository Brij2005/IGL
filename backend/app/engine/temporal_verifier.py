"""Generic persistence verifier; values are software defaults, not IGL SOPs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Literal


ThresholdSource = Literal["CONFIGURED", "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"]
VerificationState = Literal["DETECTED", "PERSISTED", "VERIFIED", "INVALIDATED", "NOT_ASSESSABLE"]


@dataclass(frozen=True)
class VerificationPolicy:
    minimum_observations: int
    minimum_duration_seconds: float
    confidence_threshold: float
    maximum_gap_seconds: float
    threshold_source: ThresholdSource
    rule_reference: str | None = None

    def __post_init__(self) -> None:
        if self.minimum_observations < 1 or self.minimum_duration_seconds < 0 or self.maximum_gap_seconds < 0:
            raise ValueError("Temporal verification values must be non-negative and observations >= 1")
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if self.threshold_source == "CONFIGURED" and not self.rule_reference:
            raise ValueError("Configured thresholds require a source reference")


@dataclass(frozen=True)
class VerificationResult:
    subject_id: str
    state: VerificationState
    observation_count: int
    duration_seconds: float
    reason: str | None
    threshold_source: ThresholdSource
    rule_reference: str | None


@dataclass
class _Candidate:
    first_seen: datetime
    last_seen: datetime
    observations: int


class TemporalVerifier:
    def __init__(self, policy: VerificationPolicy):
        self.policy = policy
        self._candidates: dict[str, _Candidate] = {}
        self._lock = RLock()

    def observe(
        self,
        subject_id: str,
        timestamp: datetime,
        positive: bool | None,
        confidence: float | None,
        *,
        assessable: bool = True,
    ) -> VerificationResult:
        if not subject_id.strip():
            raise ValueError("subject_id is required")
        observed_at = self._utc(timestamp)
        with self._lock:
            if not assessable or positive is None:
                self._candidates.pop(subject_id, None)
                return self._result(subject_id, "NOT_ASSESSABLE", 0, 0.0, "Required evidence unavailable")
            if not positive:
                candidate = self._candidates.pop(subject_id, None)
                duration = (observed_at - candidate.first_seen).total_seconds() if candidate else 0.0
                return self._result(subject_id, "INVALIDATED", candidate.observations if candidate else 0, duration, None)
            if confidence is None or not 0.0 <= confidence <= 1.0:
                raise ValueError("A finite confidence in [0, 1] is required for a positive observation")
            if confidence < self.policy.confidence_threshold:
                return self._result(subject_id, "DETECTED", 0, 0.0, "Confidence below configured threshold")

            candidate = self._candidates.get(subject_id)
            if candidate is None or (observed_at - candidate.last_seen).total_seconds() > self.policy.maximum_gap_seconds:
                candidate = _Candidate(observed_at, observed_at, 1)
                self._candidates[subject_id] = candidate
                return self._result(subject_id, "DETECTED", 1, 0.0, None)

            if observed_at <= candidate.last_seen:
                return self._result(subject_id, "PERSISTED", candidate.observations, (candidate.last_seen - candidate.first_seen).total_seconds(), "Out-of-order observation ignored")

            candidate.last_seen = observed_at
            candidate.observations += 1
            duration = (candidate.last_seen - candidate.first_seen).total_seconds()
            verified = (
                candidate.observations >= self.policy.minimum_observations
                and duration >= self.policy.minimum_duration_seconds
            )
            return self._result(subject_id, "VERIFIED" if verified else "PERSISTED", candidate.observations, duration, None)

    def reset(self, subject_id: str) -> None:
        with self._lock:
            self._candidates.pop(subject_id, None)

    def _result(self, subject_id, state, count, duration, reason):
        return VerificationResult(subject_id, state, count, duration, reason, self.policy.threshold_source, self.policy.rule_reference)

    @staticmethod
    def _utc(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)