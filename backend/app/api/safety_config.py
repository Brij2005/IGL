"""Operator configuration for detectors, safety rules, thresholds and policies.

Every write here is an operator decision recorded with provenance. None of these
endpoints run a detector, score a frame, or validate a threshold against IGL
data, and none of the responses may be read as evidence that a detector works.
"""
from __future__ import annotations

from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, require_permission, require_role
    from app.database import get_db
    from app.models import (
        Camera,
        DetectorConfig,
        EscalationPolicy,
        NotificationPolicy,
        OperatingThreshold,
        Role,
        SafetyRule,
        User,
        Zone,
    )
    from app.schemas_configuration import (
        DetectorConfigCreate,
        DetectorConfigOut,
        EscalationPolicyCreate,
        EscalationPolicyOut,
        NotificationPolicyCreate,
        NotificationPolicyOut,
        OperatingThresholdCreate,
        OperatingThresholdOut,
        SafetyRuleCreate,
        SafetyRuleOut,
    )
except ImportError:
    from backend.app.access_control import log_audit_event, require_permission, require_role
    from backend.app.database import get_db
    from backend.app.models import (
        Camera,
        DetectorConfig,
        EscalationPolicy,
        NotificationPolicy,
        OperatingThreshold,
        Role,
        SafetyRule,
        User,
        Zone,
    )
    from backend.app.schemas_configuration import (
        DetectorConfigCreate,
        DetectorConfigOut,
        EscalationPolicyCreate,
        EscalationPolicyOut,
        NotificationPolicyCreate,
        NotificationPolicyOut,
        OperatingThresholdCreate,
        OperatingThresholdOut,
        SafetyRuleCreate,
        SafetyRuleOut,
    )


router = APIRouter()
CONFIG_WRITERS = ("ADMIN", "SAFETY_OFFICER")
PAGE_DEFAULT = 100
PAGE_MAX = 500


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _resolve_scope(db: Session, *, camera_id: Optional[str], zone_id: Optional[str]) -> None:
    """Reject a configuration that points at a camera or zone that is not there."""
    if camera_id and db.query(Camera.id).filter(Camera.id == camera_id).first() is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    if zone_id and db.query(Zone.id).filter(Zone.id == zone_id).first() is None:
        raise HTTPException(status_code=404, detail="Zone not found")


def _commit_or_conflict(db: Session, instance) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A configuration with this identity already exists for that scope",
        ) from exc
    db.refresh(instance)


# ---------------------------------------------------------------------------
# Detector configuration
# ---------------------------------------------------------------------------

@router.get("/detector-configs", response_model=List[DetectorConfigOut])
def list_detector_configs(
    camera_id: str | None = None,
    zone_id: str | None = None,
    detector_key: str | None = None,
    limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("cameras:view")),
):
    """List what an operator configured, not what is currently running."""
    query = db.query(DetectorConfig)
    if camera_id:
        query = query.filter(DetectorConfig.camera_id == camera_id)
    if zone_id:
        query = query.filter(DetectorConfig.zone_id == zone_id)
    if detector_key:
        query = query.filter(DetectorConfig.detector_key == detector_key)
    return query.order_by(DetectorConfig.detector_key, DetectorConfig.created_at).offset(offset).limit(limit).all()


@router.post("/detector-configs", response_model=DetectorConfigOut, status_code=status.HTTP_201_CREATED)
def create_detector_config(
    payload: DetectorConfigCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*CONFIG_WRITERS)),
):
    _resolve_scope(db, camera_id=payload.camera_id, zone_id=payload.zone_id)
    config = DetectorConfig(
        **payload.model_dump(),
        validation_status="NOT_VALIDATED",
    )
    db.add(config)
    _commit_or_conflict(db, config)
    log_audit_event(
        db, actor.id if actor else None, "DETECTOR_CONFIG_CREATED", "DETECTOR_CONFIG", config.id,
        {
            "detector_key": config.detector_key,
            "camera_id": config.camera_id,
            "zone_id": config.zone_id,
            "is_enabled": config.is_enabled,
            "threshold_source": config.threshold_source,
        },
        _client_ip(request),
    )
    return config


@router.put("/detector-configs/{config_id}", response_model=DetectorConfigOut)
def update_detector_config(
    config_id: str,
    payload: DetectorConfigCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*CONFIG_WRITERS)),
):
    config = db.query(DetectorConfig).filter(DetectorConfig.id == config_id).first()
    if config is None:
        raise HTTPException(status_code=404, detail="Detector configuration not found")
    _resolve_scope(db, camera_id=payload.camera_id, zone_id=payload.zone_id)

    was_enabled = config.is_enabled
    for field, value in payload.model_dump().items():
        setattr(config, field, value)
    # Enabling a detector is an operator action; it is never a validation claim,
    # so the recorded validation status is preserved as NOT_VALIDATED.
    db.commit()
    db.refresh(config)

    log_audit_event(
        db, actor.id if actor else None, "DETECTOR_CONFIG_UPDATED", "DETECTOR_CONFIG", config.id,
        {
            "detector_key": config.detector_key,
            "was_enabled": was_enabled,
            "is_enabled": config.is_enabled,
            "validation_status": config.validation_status,
        },
        _client_ip(request),
    )
    return config


@router.delete("/detector-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_detector_config(
    config_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN")),
):
    config = db.query(DetectorConfig).filter(DetectorConfig.id == config_id).first()
    if config is None:
        raise HTTPException(status_code=404, detail="Detector configuration not found")
    identity = {
        "detector_key": config.detector_key,
        "camera_id": config.camera_id,
        "zone_id": config.zone_id,
    }
    db.delete(config)
    db.commit()
    log_audit_event(
        db, actor.id if actor else None, "DETECTOR_CONFIG_DELETED", "DETECTOR_CONFIG", config_id, identity, _client_ip(request)
    )


# ---------------------------------------------------------------------------
# Safety rules
# ---------------------------------------------------------------------------

@router.get("/safety-rules", response_model=List[SafetyRuleOut])
def list_safety_rules(
    zone_id: str | None = None,
    limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("zones:view")),
):
    query = db.query(SafetyRule)
    if zone_id:
        query = query.filter(SafetyRule.zone_id == zone_id)
    return query.order_by(SafetyRule.code).offset(offset).limit(limit).all()


@router.post("/safety-rules", response_model=SafetyRuleOut, status_code=status.HTTP_201_CREATED)
def create_safety_rule(
    payload: SafetyRuleCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*CONFIG_WRITERS)),
):
    _resolve_scope(db, camera_id=None, zone_id=payload.zone_id)
    rule = SafetyRule(**payload.model_dump())
    db.add(rule)
    _commit_or_conflict(db, rule)
    log_audit_event(
        db, actor.id if actor else None, "SAFETY_RULE_CREATED", "SAFETY_RULE", rule.id,
        {
            "code": rule.code,
            "zone_id": rule.zone_id,
            "validation_status": rule.validation_status,
            "source_reference": rule.source_reference,
        },
        _client_ip(request),
    )
    return rule


# ---------------------------------------------------------------------------
# Operating thresholds
# ---------------------------------------------------------------------------

@router.get("/operating-thresholds", response_model=List[OperatingThresholdOut])
def list_operating_thresholds(
    zone_id: str | None = None,
    camera_id: str | None = None,
    limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("zones:view")),
):
    query = db.query(OperatingThreshold)
    if zone_id:
        query = query.filter(OperatingThreshold.zone_id == zone_id)
    if camera_id:
        query = query.filter(OperatingThreshold.camera_id == camera_id)
    return query.order_by(OperatingThreshold.code).offset(offset).limit(limit).all()


@router.post("/operating-thresholds", response_model=OperatingThresholdOut, status_code=status.HTTP_201_CREATED)
def create_operating_threshold(
    payload: OperatingThresholdCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*CONFIG_WRITERS)),
):
    _resolve_scope(db, camera_id=payload.camera_id, zone_id=payload.zone_id)
    threshold = OperatingThreshold(**payload.model_dump())
    db.add(threshold)
    _commit_or_conflict(db, threshold)
    log_audit_event(
        db, actor.id if actor else None, "OPERATING_THRESHOLD_CREATED", "OPERATING_THRESHOLD", threshold.id,
        {
            "code": threshold.code,
            "value": threshold.value,
            "threshold_source": threshold.threshold_source,
            "validation_status": threshold.validation_status,
        },
        _client_ip(request),
    )
    return threshold


# ---------------------------------------------------------------------------
# Escalation policies
# ---------------------------------------------------------------------------

@router.get("/escalation-policies", response_model=List[EscalationPolicyOut])
def list_escalation_policies(
    limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    return db.query(EscalationPolicy).order_by(EscalationPolicy.escalation_level.desc(), EscalationPolicy.name).offset(offset).limit(limit).all()


@router.post("/escalation-policies", response_model=EscalationPolicyOut, status_code=status.HTTP_201_CREATED)
def create_escalation_policy(
    payload: EscalationPolicyCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*CONFIG_WRITERS)),
):
    if db.query(Role.name).filter(Role.name == payload.to_role).first() is None:
        raise HTTPException(status_code=404, detail=f"Role '{payload.to_role}' does not exist")
    _resolve_scope(db, camera_id=payload.camera_id, zone_id=payload.zone_id)
    policy = EscalationPolicy(**payload.model_dump(), created_by_user_id=actor.id if actor else None)
    db.add(policy)
    db.commit()
    db.refresh(policy)
    log_audit_event(
        db, actor.id if actor else None, "ESCALATION_POLICY_CREATED", "ESCALATION_POLICY", policy.id,
        {
            "name": policy.name,
            "to_role": policy.to_role,
            "escalate_after_seconds": policy.escalate_after_seconds,
        },
        _client_ip(request),
    )
    return policy


# ---------------------------------------------------------------------------
# Notification policies
# ---------------------------------------------------------------------------

@router.get("/notification-policies", response_model=List[NotificationPolicyOut])
def list_notification_policies(
    limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    return db.query(NotificationPolicy).order_by(NotificationPolicy.name).offset(offset).limit(limit).all()


@router.post("/notification-policies", response_model=NotificationPolicyOut, status_code=status.HTTP_201_CREATED)
def create_notification_policy(
    payload: NotificationPolicyCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*CONFIG_WRITERS)),
):
    if db.query(Role.name).filter(Role.name == payload.recipient_role).first() is None:
        raise HTTPException(status_code=404, detail=f"Role '{payload.recipient_role}' does not exist")
    _resolve_scope(db, camera_id=None, zone_id=payload.zone_id)
    policy = NotificationPolicy(**payload.model_dump())
    db.add(policy)
    db.commit()
    db.refresh(policy)
    log_audit_event(
        db, actor.id if actor else None, "NOTIFICATION_POLICY_CREATED", "NOTIFICATION_POLICY", policy.id,
        {"name": policy.name, "channel": policy.channel, "recipient_role": policy.recipient_role},
        _client_ip(request),
    )
    return policy
