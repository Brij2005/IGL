"""
SQLAlchemy ORM models for IGL Industrial AI Safety & Incident Intelligence Platform.

Design principles:
- Plant -> Area -> Zone -> Camera hierarchy
- Computer Vision: Camera -> Track -> Detection -> Event -> EventEvidence
- Safety Workflow: Event -> Acknowledgement -> Assignment -> Incident / NearMiss -> CorrectiveAction
- Model Tracking: ModelVersion -> ModelMetric
- Visual Track ID is NOT employee identity. Identity fields are strictly nullable.
- Observation states: CONFIRMED, POSSIBLE, NOT_ASSESSABLE, NOT_VALIDATED.
- Workflow states: NEW, UNACKNOWLEDGED, ACKNOWLEDGED, ASSIGNED, UNDER_INVESTIGATION, ACTION_REQUIRED, RESOLVED, CLOSED.
- Zero fake metrics, zero dummy seed incidents, configurable IGL validation.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text,
    ForeignKey, Index, UniqueConstraint, CheckConstraint, JSON
)
from sqlalchemy.orm import relationship

try:
    from app.database import Base
except ImportError:
    try:
        from backend.app.database import Base
    except ImportError:
        from sqlalchemy.orm import declarative_base
        Base = declarative_base()


def generate_uuid() -> str:
    """Generate a standard string UUID4."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


# ============================================================================
# 1. HIERARCHY DOMAIN MODELS
# ============================================================================

class Plant(Base):
    """Manufacturing or processing plant (e.g., IGL Kashipur Plant)."""
    __tablename__ = "plants"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    location = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    areas = relationship("Area", back_populates="plant", cascade="all, delete-orphan")


class Area(Base):
    """Plant area/department (e.g., Chemical Processing Area, Warehouse)."""
    __tablename__ = "areas"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    plant_id = Column(String(36), ForeignKey("plants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), nullable=False, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    plant = relationship("Plant", back_populates="areas")
    zones = relationship("Zone", back_populates="area", cascade="all, delete-orphan")


class Zone(Base):
    """Specific functional safety zone within an area (e.g., High-Voltage Zone)."""
    __tablename__ = "zones"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    area_id = Column(String(36), ForeignKey("areas.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), nullable=False, index=True)
    zone_type = Column(String(50), default="WORK_AREA", nullable=False)  # RESTRICTED, HAZARDOUS, PPE_MANDATORY, WORK_AREA
    geometry_json = Column(JSON, nullable=True)  # Polygon coordinates e.g. [[x1,y1], [x2,y2], ...]
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    area = relationship("Area", back_populates="zones")
    cameras = relationship("Camera", back_populates="zone")
    ppe_rules = relationship("PPERule", back_populates="zone", cascade="all, delete-orphan")
    equipments = relationship("Equipment", back_populates="zone")
    events = relationship("Event", back_populates="zone")


class Camera(Base):
    """Industrial CCTV / IP / RTSP camera attached to a safety zone."""
    __tablename__ = "cameras"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    stream_url = Column(String(500), nullable=False)
    camera_type = Column(String(50), default="RTSP", nullable=False)  # RTSP, IP, PTZ, FIXED, USB, FILE
    fps = Column(Float, default=25.0, nullable=False)
    resolution = Column(String(20), default="1920x1080", nullable=False)
    location_description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    zone = relationship("Zone", back_populates="cameras")
    health = relationship("CameraHealth", back_populates="camera", uselist=False, cascade="all, delete-orphan")
    tracks = relationship("Track", back_populates="camera", cascade="all, delete-orphan")
    detections = relationship("Detection", back_populates="camera", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="camera", cascade="all, delete-orphan")


class CameraHealth(Base):
    """Real-time operational & image quality metrics for a camera feed."""
    __tablename__ = "camera_health"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    status = Column(String(50), default="UNKNOWN", nullable=False)  # ONLINE, DEGRADED, OFFLINE, UNRELIABLE, UNKNOWN
    last_frame_timestamp = Column(DateTime(timezone=True), nullable=True)
    fps = Column(Float, default=0.0, nullable=False)
    latency_ms = Column(Float, default=0.0, nullable=False)
    is_frozen = Column(Boolean, default=False, nullable=False)
    is_black = Column(Boolean, default=False, nullable=False)
    image_quality_score = Column(Float, default=1.0, nullable=False)
    inference_status = Column(String(50), default="IDLE", nullable=False)
    health_timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    camera = relationship("Camera", back_populates="health")


# ============================================================================
# 2. USER, ROLE & IDENTITY DOMAIN MODELS
# ============================================================================

class Role(Base):
    """Role-based Access Control (RBAC) role definition."""
    __tablename__ = "roles"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(50), unique=True, nullable=False, index=True)  # ADMIN, SAFETY_OFFICER, PLANT_MANAGER, OPERATOR
    description = Column(Text, nullable=True)
    permissions_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    users = relationship("User", back_populates="role")


class User(Base):
    """Platform user account for access control and event assignment."""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    role_id = Column(String(36), ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(150), nullable=False)
    employee_code = Column(String(50), nullable=True, index=True)  # Optional enterprise ID
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    role = relationship("Role", back_populates="users")
    tracks = relationship("Track", back_populates="employee")
    acknowledgements = relationship("Acknowledgement", back_populates="user")
    assigned_tasks = relationship("Assignment", foreign_keys="[Assignment.assigned_to_user_id]", back_populates="assignee")
    created_assignments = relationship("Assignment", foreign_keys="[Assignment.assigned_by_user_id]", back_populates="assigner")
    reported_incidents = relationship("Incident", back_populates="reporter")
    corrective_actions = relationship("CorrectiveAction", back_populates="assignee")
    notifications = relationship("Notification", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")


# ============================================================================
# 3. SAFETY SOP RULES & EQUIPMENT
# ============================================================================

class PPERule(Base):
    """PPE mandate rule per safety zone."""
    __tablename__ = "ppe_rules"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="CASCADE"), nullable=True, index=True)
    ppe_type = Column(String(50), nullable=False)  # HELMET, SAFETY_VEST, GOGGLES, GLOVES, SAFETY_FOOTWEAR, RESPIRATOR, FACE_SHIELD, EAR_PROTECTION
    is_mandatory = Column(Boolean, default=True, nullable=False)
    min_confidence = Column(Float, default=0.75, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    zone = relationship("Zone", back_populates="ppe_rules")


class Equipment(Base):
    """Monitored industrial equipment or machinery in a safety zone."""
    __tablename__ = "equipments"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    equipment_type = Column(String(50), nullable=False)  # FORKLIFT, BOILER, CONVEYOR, REACTOR
    serial_number = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    zone = relationship("Zone", back_populates="equipments")


# ============================================================================
# 4. COMPUTER VISION & PERCEPTION MODELS
# ============================================================================

class Track(Base):
    """
    Visual object tracking session (AI track identity).
    NOTE: Track ID represents a visual tracking session, NOT an employee identity.
    The employee_id field is strictly NULLABLE and unlinked by default.
    """
    __tablename__ = "tracks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True)
    track_uuid = Column(String(100), nullable=False, index=True)
    object_class = Column(String(50), default="PERSON", nullable=False)  # PERSON, VEHICLE, FORKLIFT, MACHINERY
    first_seen_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    employee_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)  # STRICTLY NULL BY DEFAULT
    metadata_json = Column(JSON, nullable=True)

    # Relationships
    camera = relationship("Camera", back_populates="tracks")
    employee = relationship("User", back_populates="tracks")
    detections = relationship("Detection", back_populates="track")
    events = relationship("Event", back_populates="track")


class Detection(Base):
    """Raw single-frame object detection result from an AI model."""
    __tablename__ = "detections"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id = Column(String(36), ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    object_class = Column(String(50), nullable=False)
    confidence = Column(Float, nullable=False)
    bbox_json = Column(JSON, nullable=False)  # [x1, y1, x2, y2] normalized or pixel coords
    observation_state = Column(String(50), default="CONFIRMED", nullable=False)  # CONFIRMED, POSSIBLE, NOT_ASSESSABLE, NOT_VALIDATED
    metadata_json = Column(JSON, nullable=True)

    # Relationships
    camera = relationship("Camera", back_populates="detections")
    track = relationship("Track", back_populates="detections")


class Event(Base):
    """Verified safety event generated after temporal verification."""
    __tablename__ = "events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True)
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True)
    track_id = Column(String(36), ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(100), nullable=False, index=True)  # PPE_NON_COMPLIANCE, ZONE_ENTRY, NEAR_MISS, FALL, FIRE, SMOKE
    observation_state = Column(String(50), default="CONFIRMED", nullable=False)  # CONFIRMED, POSSIBLE, NOT_ASSESSABLE, NOT_VALIDATED
    severity = Column(String(20), default="MEDIUM", nullable=False, index=True)  # LOW, MEDIUM, HIGH, CRITICAL
    workflow_state = Column(String(50), default="NEW", nullable=False, index=True)  # NEW, UNACKNOWLEDGED, ACKNOWLEDGED, ASSIGNED, UNDER_INVESTIGATION, ACTION_REQUIRED, RESOLVED, CLOSED
    confidence = Column(Float, nullable=False)
    duration_seconds = Column(Float, default=0.0, nullable=False)
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    model_version = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    camera = relationship("Camera", back_populates="events")
    zone = relationship("Zone", back_populates="events")
    track = relationship("Track", back_populates="events")
    evidences = relationship("EventEvidence", back_populates="event", cascade="all, delete-orphan")
    acknowledgements = relationship("Acknowledgement", back_populates="event", cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="event", cascade="all, delete-orphan")
    incidents = relationship("Incident", back_populates="event")
    near_misses = relationship("NearMiss", back_populates="event")
    corrective_actions = relationship("CorrectiveAction", back_populates="event")
    notifications = relationship("Notification", back_populates="event")


class EventEvidence(Base):
    """Cryptographically or path-referenced evidence (snapshots, video clips)."""
    __tablename__ = "event_evidence"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_type = Column(String(50), default="SNAPSHOT", nullable=False)  # SNAPSHOT, VIDEO_CLIP, HEATMAP
    file_path = Column(String(500), nullable=False)
    file_hash = Column(String(64), nullable=True)  # SHA-256 for tampering verification
    thumbnail_path = Column(String(500), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    event = relationship("Event", back_populates="evidences")


# ============================================================================
# 5. WORKFLOW, INCIDENT & RESPONSE DOMAIN MODELS
# ============================================================================

class Incident(Base):
    """Escalated official safety incident record."""
    __tablename__ = "incidents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(20), default="HIGH", nullable=False)
    status = Column(String(50), default="OPEN", nullable=False)  # OPEN, INVESTIGATING, CLOSED
    reported_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    event = relationship("Event", back_populates="incidents")
    reporter = relationship("User", back_populates="reported_incidents")
    corrective_actions = relationship("CorrectiveAction", back_populates="incident")


class NearMiss(Base):
    """Identified hazardous near-miss candidate."""
    __tablename__ = "near_misses"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    potential_severity = Column(String(20), default="HIGH", nullable=False)
    interaction_type = Column(String(100), nullable=True)  # e.g., PERSON_VEHICLE_PROXIMITY
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    event = relationship("Event", back_populates="near_misses")
    corrective_actions = relationship("CorrectiveAction", back_populates="near_miss")


class Acknowledgement(Base):
    """Audit log of user acknowledging an unacknowledged event."""
    __tablename__ = "acknowledgements"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    acknowledged_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    notes = Column(Text, nullable=True)

    # Relationships
    event = relationship("Event", back_populates="acknowledgements")
    user = relationship("User", back_populates="acknowledgements")


class Assignment(Base):
    """Task assignment of a safety event to a responsible user/officer."""
    __tablename__ = "assignments"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_to_user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    event = relationship("Event", back_populates="assignments")
    assignee = relationship("User", foreign_keys=[assigned_to_user_id], back_populates="assigned_tasks")
    assigner = relationship("User", foreign_keys=[assigned_by_user_id], back_populates="created_assignments")


class CorrectiveAction(Base):
    """Action item required to resolve or mitigate a safety issue."""
    __tablename__ = "corrective_actions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True)
    incident_id = Column(String(36), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True)
    near_miss_id = Column(String(36), ForeignKey("near_misses.id", ondelete="SET NULL"), nullable=True, index=True)
    action_description = Column(Text, nullable=False)
    assigned_to_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(50), default="PENDING", nullable=False)  # PENDING, IN_PROGRESS, VERIFIED, CLOSED
    due_date = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    event = relationship("Event", back_populates="corrective_actions")
    incident = relationship("Incident", back_populates="corrective_actions")
    near_miss = relationship("NearMiss", back_populates="corrective_actions")
    assignee = relationship("User", back_populates="corrective_actions")


class Notification(Base):
    """Multi-channel alert dispatch tracking record."""
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = Column(String(50), default="DASHBOARD", nullable=False)  # DASHBOARD, EMAIL, SMS, TEAMS, WEBHOOK, BUZZER
    recipient = Column(String(255), nullable=True)
    status = Column(String(50), default="PENDING", nullable=False)  # PENDING, SENT, FAILED, RETRYING
    sent_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    event = relationship("Event", back_populates="notifications")
    user = relationship("User", back_populates="notifications")


class AuditLog(Base):
    """Immutable platform security and system activity audit trail."""
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False, index=True)
    resource_id = Column(String(36), nullable=True)
    details_json = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="audit_logs")


# ============================================================================
# 6. MODEL REGISTRY & METRICS
# ============================================================================

class ModelVersion(Base):
    """Deplomatic registry for AI model versions."""
    __tablename__ = "model_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    model_name = Column(String(100), nullable=False, index=True)
    version = Column(String(50), nullable=False)
    task_type = Column(String(50), nullable=False)  # PPE_DETECTOR, PERSON_TRACKER, FIRE_SMOKE, FALL_DETECTOR
    weights_path = Column(String(500), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    deployed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    metrics = relationship("ModelMetric", back_populates="model_version", cascade="all, delete-orphan")


class ModelMetric(Base):
    """Validated accuracy metrics for AI models on benchmark/IGL datasets."""
    __tablename__ = "model_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    model_version_id = Column(String(36), ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_name = Column(String(100), nullable=False)
    precision = Column(Float, nullable=True)
    recall = Column(Float, nullable=True)
    f1_score = Column(Float, nullable=True)
    map50 = Column(Float, nullable=True)
    fps = Column(Float, nullable=True)
    evaluated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    is_igl_validated = Column(Boolean, default=False, nullable=False)  # Marked True ONLY when validated on real IGL data

    # Relationships
    model_version = relationship("ModelVersion", back_populates="metrics")
