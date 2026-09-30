"""Authenticated operator-supplied plant, area, zone, and PPE configuration."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

try:
    from app.auth import log_audit_event, require_permission, require_role
    from app.database import get_db
    from app.models import Area, Plant, PPERule, User, Zone
    from app.schemas_configuration import (
        AreaCreate, AreaOut, PlantCreate, PlantOut, PPERuleCreate, PPERuleOut, ZoneCreate, ZoneOut,
    )
    from app.services.zone_engine import validate_polygon
except ImportError:
    from backend.app.auth import log_audit_event, require_permission, require_role
    from backend.app.database import get_db
    from backend.app.models import Area, Plant, PPERule, User, Zone
    from backend.app.schemas_configuration import (
        AreaCreate, AreaOut, PlantCreate, PlantOut, PPERuleCreate, PPERuleOut, ZoneCreate, ZoneOut,
    )
    from backend.app.services.zone_engine import validate_polygon


router = APIRouter()


@router.get("/plants", response_model=list[PlantOut])
def list_plants(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(require_permission("plants:view"))):
    return db.query(Plant).order_by(Plant.name).offset(offset).limit(limit).all()


@router.post("/plants", response_model=PlantOut, status_code=201)
def create_plant(payload: PlantCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN"))):
    plant = Plant(**payload.model_dump())
    db.add(plant)
    db.commit()
    db.refresh(plant)
    log_audit_event(db, user.id, "PLANT_CONFIGURATION_CREATED", "PLANT", plant.id, {"code": plant.code}, request.client.host if request.client else None)
    return plant


@router.get("/areas", response_model=list[AreaOut])
def list_areas(plant_id: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(require_permission("plants:view"))):
    query = db.query(Area)
    if plant_id:
        query = query.filter(Area.plant_id == plant_id)
    return query.order_by(Area.name).offset(offset).limit(limit).all()


@router.post("/areas", response_model=AreaOut, status_code=201)
def create_area(payload: AreaCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN", "SAFETY_OFFICER"))):
    if db.query(Plant.id).filter(Plant.id == payload.plant_id, Plant.is_active.is_(True)).first() is None:
        raise HTTPException(status_code=404, detail="Active plant not found")
    area = Area(**payload.model_dump())
    db.add(area)
    db.commit()
    db.refresh(area)
    log_audit_event(db, user.id, "AREA_CONFIGURATION_CREATED", "AREA", area.id, {"plant_id": area.plant_id, "code": area.code}, request.client.host if request.client else None)
    return area


@router.get("/zones", response_model=list[ZoneOut])
def list_zones(area_id: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(require_permission("zones:view"))):
    query = db.query(Zone)
    if area_id:
        query = query.filter(Zone.area_id == area_id)
    return query.order_by(Zone.name).offset(offset).limit(limit).all()


@router.post("/zones", response_model=ZoneOut, status_code=201)
def create_zone(payload: ZoneCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN", "SAFETY_OFFICER"))):
    if db.query(Area.id).filter(Area.id == payload.area_id).first() is None:
        raise HTTPException(status_code=404, detail="Area not found")
    if payload.geometry_json is not None and validate_polygon(payload.geometry_json) is None:
        raise HTTPException(status_code=422, detail="Zone polygon must contain at least three finite points")
    zone = Zone(**payload.model_dump())
    db.add(zone)
    db.commit()
    db.refresh(zone)
    log_audit_event(db, user.id, "ZONE_CONFIGURATION_CREATED", "ZONE", zone.id, {"area_id": zone.area_id, "zone_type": zone.zone_type, "code": zone.code}, request.client.host if request.client else None)
    return zone


@router.get("/ppe-rules", response_model=list[PPERuleOut])
def list_ppe_rules(zone_id: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(require_permission("zones:view"))):
    query = db.query(PPERule)
    if zone_id:
        query = query.filter(PPERule.zone_id == zone_id)
    return query.order_by(PPERule.created_at.desc()).offset(offset).limit(limit).all()


@router.post("/ppe-rules", response_model=PPERuleOut, status_code=201)
def create_ppe_rule(payload: PPERuleCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN", "SAFETY_OFFICER"))):
    if db.query(Zone.id).filter(Zone.id == payload.zone_id, Zone.is_active.is_(True)).first() is None:
        raise HTTPException(status_code=404, detail="Active zone not found")
    rule = PPERule(**payload.model_dump(), validation_status="NOT_VALIDATED")
    db.add(rule)
    db.commit()
    db.refresh(rule)
    log_audit_event(db, user.id, "PPE_RULE_CONFIGURATION_CREATED", "PPE_RULE", rule.id, {"zone_id": rule.zone_id, "ppe_type": rule.ppe_type, "threshold_source": rule.threshold_source}, request.client.host if request.client else None)
    return rule