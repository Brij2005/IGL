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
    from app.utils.encrypted_url import EncryptedCameraURL
except ImportError:
    from backend.app.utils.encrypted_url import EncryptedCameraURL

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
    detector_configs = relationship("DetectorConfig", back_populates="zone")
    safety_rules = relationship("SafetyRule", back_populates="zone")
    operating_thresholds = relationship("OperatingThreshold", back_populates="zone")
    escalation_policies = relationship("EscalationPolicy", back_populates="zone")
    notification_policies = relationship("NotificationPolicy", back_populates="zone")
    alarms = relationship("Alarm", back_populates="zone")
    event_correlations = relationship("EventCorrelation", back_populates="zone")


class Camera(Base):
    """Industrial CCTV / IP / RTSP camera attached to a safety zone."""
    __tablename__ = "cameras"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    stream_url = Column(EncryptedCameraURL(1200), nullable=False)
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
    evidences = relationship("EventEvidence", back_populates="camera")
    detector_configs = relationship("DetectorConfig", back_populates="camera")
    operating_thresholds = relationship("OperatingThreshold", back_populates="camera")
    escalation_policies = relationship("EscalationPolicy", back_populates="camera")
    event_correlations = relationship("EventCorrelation", back_populates="camera")
    alarms = relationship("Alarm", back_populates="camera")


class CameraHealth(Base):
    """Real-time operational & image quality metrics for a camera feed.

    Every measured column is nullable and stays NULL until a frame has actually
    been observed. ``is_black``/``is_frozen`` are nullable for the same reason:
    a camera that has never produced a frame must not report "not black" and
    "not frozen" as if those had been observed.
    """
    __tablename__ = "camera_health"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    # NEVER_EVALUATED, CONFIGURED, CONNECTING, ONLINE, STALE, BLACK_FRAME,
    # FROZEN, OFFLINE
    status = Column(String(50), default="NEVER_EVALUATED", nullable=False)
    last_frame_timestamp = Column(DateTime(timezone=True), nullable=True)
    # Legacy unmeasured placeholders. Superseded by measured_fps and
    # frame_latency_ms; they stay NULL until a real measurement exists so no
    # API or report can present a fabricated zero as an observed value.
    fps = Column(Float, nullable=True)
    latency_ms = Column(Float, nullable=True)
    is_frozen = Column(Boolean, nullable=True)
    is_black = Column(Boolean, nullable=True)
    image_quality_score = Column(Float, nullable=True)
    inference_status = Column(String(50), default="NOT_RUNNING", nullable=False)
    health_timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    measured_fps = Column(Float, nullable=True)
    frame_latency_ms = Column(Float, nullable=True)
    observed_resolution = Column(String(20), nullable=True)
    dropped_frames = Column(Integer, nullable=True)
    brightness_score = Column(Float, nullable=True)
    sharpness_score = Column(Float, nullable=True)

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
    """Operator identity record.

    A user is an operator account when ``hashed_password`` is set. Development
    directory records may remain passwordless while anonymous access is enabled.
    The row also serves operational functions:

    * it names who an event was assigned to and who reported an incident,
    * it identifies the actor on audit and state-transition rows,
    * it carries the role used to resolve authorization and notification audiences.

    ``hashed_password`` is nullable so older passwordless directory records do
    not accidentally become login accounts.
    """
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    role_id = Column(String(36), ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    # Bcrypt password hash; never included in API response schemas.
    hashed_password = Column(String(255), nullable=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    full_name = Column(String(150), nullable=False)
    employee_code = Column(String(50), nullable=True, index=True)  # Optional enterprise ID
    is_active = Column(Boolean, default=True, nullable=False)
    # Deactivated identities cannot sign in or receive assigned work.
    deactivated_at = Column(DateTime(timezone=True), nullable=True)
    deactivated_by_user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_users_deactivated_by_user_id"),
        nullable=True,
        index=True,
    )
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
    acknowledged_alarms = relationship("Alarm", foreign_keys="[Alarm.acknowledged_by_user_id]", back_populates="acknowledged_by")


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
    min_confidence = Column(Float, nullable=True)
    source_reference = Column(String(500), nullable=True)
    threshold_source = Column(String(80), default="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION", nullable=False)
    validation_status = Column(String(50), default="NOT_VALIDATED", nullable=False)
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
    alarms = relationship("Alarm", back_populates="track")


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
    observation_state = Column(String(50), default="NOT_VALIDATED", nullable=False)  # CONFIRMED, POSSIBLE, NOT_ASSESSABLE, NOT_VALIDATED
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
    observation_state = Column(String(50), default="NOT_VALIDATED", nullable=False)  # CONFIRMED, POSSIBLE, NOT_ASSESSABLE, NOT_VALIDATED
    severity = Column(String(20), default="MEDIUM", nullable=False, index=True)  # LOW, MEDIUM, HIGH, CRITICAL
    workflow_state = Column(String(50), default="NEW", nullable=False, index=True)  # NEW, UNACKNOWLEDGED, ACKNOWLEDGED, ASSIGNED, UNDER_INVESTIGATION, ACTION_REQUIRED, RESOLVED, CLOSED
    confidence = Column(Float, nullable=True)  # NULL when observation_state is NOT_ASSESSABLE
    duration_seconds = Column(Float, nullable=True)  # NULL until a real duration is measured
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    model_version = Column(String(50), nullable=True)

    # Detection provenance. An event that cannot name the detector, the temporal
    # verdict, the thresholds and the weights that produced it cannot be audited,
    # so every field here stays NULL when the corresponding evidence is absent.
    detector_key = Column(String(50), nullable=True, index=True)
    # TemporalVerifier states: DETECTED, PERSISTED, VERIFIED, INVALIDATED,
    # NOT_ASSESSABLE. NULL means no temporal verification was performed.
    verification_state = Column(String(40), nullable=True, index=True)
    temporal_observations = Column(Integer, nullable=True)
    temporal_duration_seconds = Column(Float, nullable=True)
    threshold_source = Column(String(80), nullable=True)
    source_reference = Column(String(500), nullable=True)
    model_name = Column(String(100), nullable=True)
    model_weights_checksum = Column(String(64), nullable=True)
    provenance_json = Column(JSON, nullable=True)
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
    state_transitions = relationship("EventStateTransition", back_populates="event", cascade="all, delete-orphan")
    escalations = relationship("EventEscalation", back_populates="event", cascade="all, delete-orphan")
    alarms = relationship("Alarm", back_populates="event", cascade="all, delete-orphan")


class EventEvidence(Base):
    """Cryptographically or path-referenced evidence (snapshots, video clips).

    Evidence is only ever written from a real frame captured for a real event.
    ``retention_expires_at`` is NULL when no retention policy is configured,
    which means "retained indefinitely until an operator deletes it", not
    "expires immediately".
    """
    __tablename__ = "event_evidence"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_type = Column(String(50), default="SNAPSHOT", nullable=False)  # SNAPSHOT, VIDEO_CLIP, HEATMAP
    file_path = Column(String(500), nullable=False)
    file_hash = Column(String(64), nullable=True)  # SHA-256 for tampering verification
    thumbnail_path = Column(String(500), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    camera_id = Column(
        String(36),
        ForeignKey("cameras.id", ondelete="SET NULL", name="fk_event_evidence_camera_id"),
        nullable=True,
        index=True,
    )
    retention_policy = Column(String(50), nullable=True)
    retention_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    event = relationship("Event", back_populates="evidences")
    camera = relationship("Camera", back_populates="evidences")


class EventStateTransition(Base):
    """Auditable record of an allowed event workflow transition."""
    __tablename__ = "event_state_transitions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    previous_state = Column(String(50), nullable=False)
    new_state = Column(String(50), nullable=False)
    reason = Column(Text, nullable=False)
    transitioned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    event = relationship("Event", back_populates="state_transitions")
    user = relationship("User")


class IncidentStateTransition(Base):
    """Auditable record of an allowed incident workflow transition."""
    __tablename__ = "incident_state_transitions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    incident_id = Column(String(36), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    previous_state = Column(String(50), nullable=False)
    new_state = Column(String(50), nullable=False)
    reason = Column(Text(), nullable=False)
    transitioned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    incident = relationship("Incident", back_populates="state_transitions")
    user = relationship("User")


class NearMissStateTransition(Base):
    """Auditable record of an allowed near-miss workflow transition."""
    __tablename__ = "near_miss_state_transitions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    near_miss_id = Column(String(36), ForeignKey("near_misses.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    previous_state = Column(String(50), nullable=False)
    new_state = Column(String(50), nullable=False)
    reason = Column(Text(), nullable=False)
    transitioned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    near_miss = relationship("NearMiss", back_populates="state_transitions")
    user = relationship("User")


class CorrectiveActionStateTransition(Base):
    """Auditable record of an allowed corrective-action workflow transition."""
    __tablename__ = "corrective_action_state_transitions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    corrective_action_id = Column(String(36), ForeignKey("corrective_actions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    previous_state = Column(String(50), nullable=False)
    new_state = Column(String(50), nullable=False)
    reason = Column(Text(), nullable=False)
    transitioned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    corrective_action = relationship("CorrectiveAction", back_populates="state_transitions")
    user = relationship("User")


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
    state_transitions = relationship("IncidentStateTransition", back_populates="incident", cascade="all, delete-orphan")
    alarms = relationship("Alarm", back_populates="incident")


class NearMiss(Base):
    """Identified hazardous near-miss candidate."""
    __tablename__ = "near_misses"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    potential_severity = Column(String(20), default="HIGH", nullable=False)
    interaction_type = Column(String(100), nullable=True)  # e.g., PERSON_VEHICLE_PROXIMITY
    status = Column(String(50), default="REPORTED", nullable=False, index=True)  # REPORTED, UNDER_REVIEW, CONFIRMED, DISMISSED, CLOSED
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    event = relationship("Event", back_populates="near_misses")
    corrective_actions = relationship("CorrectiveAction", back_populates="near_miss")
    state_transitions = relationship("NearMissStateTransition", back_populates="near_miss", cascade="all, delete-orphan")


class Acknowledgement(Base):
    """Audit log of an event being handled.

    ``user_id`` is nullable because nothing in this build authenticates a
    request, so the acting identity is normally unknown. The acknowledgement is
    still recorded: the fact that the event was handled is real even when the
    platform cannot prove who handled it. The row is visibly unattributed
    rather than attributed to a person who may not have acted.
    """
    __tablename__ = "acknowledgements"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
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
    state_transitions = relationship("CorrectiveActionStateTransition", back_populates="corrective_action", cascade="all, delete-orphan")


class Notification(Base):
    """Multi-channel alert dispatch tracking record.

    Status values: QUEUED, SENDING, SENT, RETRYING, FAILED, NOT_CONFIGURED,
    NOT_IMPLEMENTED.
    A channel may only report SENT after the configured provider accepts it;
    this is not proof that a recipient read or received it.
    """
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True)
    # No request is authenticated in this build, so a notification is addressed to
    # a role or a label rather than to a signed-in user. user_id stays for a
    # specific named recipient and is NULL when the audience is a role.
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    recipient_role = Column(String(50), nullable=True, index=True)
    channel = Column(String(50), default="DASHBOARD", nullable=False)  # DASHBOARD, EMAIL, SMS, TEAMS, WEBHOOK, BUZZER
    recipient = Column(String(255), nullable=True)
    status = Column(String(50), default="QUEUED", nullable=False, index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    # Delivery bookkeeping. retry_count and next_attempt_at make a retry schedule
    # auditable instead of a silent background retry.
    retry_count = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    # Deduplication key so one event does not produce an unbounded alert storm.
    dedup_key = Column(String(255), nullable=True, index=True)
    provider = Column(String(50), nullable=True)
    provider_message_id = Column(String(255), nullable=True)
    payload_summary = Column(Text, nullable=True)
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
# 5b. ALARM SUBSYSTEM
# ============================================================================

class Alarm(Base):
    """A software alarm raised from one real, confirmed safety event.

    An alarm row is never created without a persisted event that the temporal
    verifier already marked CONFIRMED. The row records why it was raised, which
    policy allowed or suppressed it, and what the physical actuator did. The
    physical fields stay NULL or NOT_ATTEMPTED until a real transport call runs,
    so an alarm can never imply that a siren or relay fired when nothing was
    wired.

    States: ACTIVE, ACKNOWLEDGED, ESCALATED, SUPPRESSED, EXPIRED, CLEARED.
    """
    __tablename__ = "alarms"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True, index=True)
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True)
    track_id = Column(String(36), ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True)
    incident_id = Column(String(36), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True)
    severity = Column(String(20), default="MEDIUM", nullable=False, index=True)
    state = Column(String(30), default="ACTIVE", nullable=False, index=True)
    # The detector and rule that caused the alarm, plus the confidence and model
    # that produced the underlying event.
    detector_key = Column(String(50), nullable=True, index=True)
    confidence = Column(Float, nullable=True)
    model_name = Column(String(100), nullable=True)
    model_version = Column(String(50), nullable=True)
    reason = Column(String(500), nullable=True)
    provenance_json = Column(JSON, nullable=True)

    # Alarm policy accounting. repeat_count and suppression_count make repeated
    # alarm suppression auditable rather than invisible.
    raised_count = Column(Integer, default=1, nullable=False)
    suppression_count = Column(Integer, default=0, nullable=False)
    cooldown_until = Column(DateTime(timezone=True), nullable=True, index=True)
    raised_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    cleared_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Physical actuation facts. physical_state is PHYSICAL_ALARM_NOT_CONFIGURED,
    # ACTUATOR_NOT_CONNECTED, ACTIVATION_ATTEMPTED, or ACTIVATED. ACTIVATED is
    # only ever written from a real successful transport call.
    physical_state = Column(String(50), default="PHYSICAL_ALARM_NOT_CONFIGURED", nullable=False)
    physical_result = Column(String(255), nullable=True)
    physical_activated_at = Column(DateTime(timezone=True), nullable=True)
    physical_cleared_at = Column(DateTime(timezone=True), nullable=True)

    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    event = relationship("Event", back_populates="alarms")
    camera = relationship("Camera", back_populates="alarms")
    zone = relationship("Zone", back_populates="alarms")
    track = relationship("Track", back_populates="alarms")
    incident = relationship("Incident", back_populates="alarms")
    acknowledged_by = relationship("User", foreign_keys=[acknowledged_by_user_id], back_populates="acknowledged_alarms")
    state_transitions = relationship("AlarmStateTransition", back_populates="alarm", cascade="all, delete-orphan")


class AlarmStateTransition(Base):
    """Auditable record of an allowed alarm state transition."""
    __tablename__ = "alarm_state_transitions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    alarm_id = Column(String(36), ForeignKey("alarms.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    previous_state = Column(String(30), nullable=True)
    new_state = Column(String(30), nullable=False)
    reason = Column(Text, nullable=False)
    transitioned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    alarm = relationship("Alarm", back_populates="state_transitions")
    user = relationship("User")


# ============================================================================
# 6. MODEL REGISTRY & METRICS
# ============================================================================

class ModelVersion(Base):
    """Deployment registry for AI model versions."""
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


# ============================================================================
# 7. SAFETY POLICY, DETECTOR CONFIGURATION, ESCALATION & CORRELATION
# ============================================================================

class DetectorConfig(Base):
    """Operator configuration for one safety detector on a camera or zone.

    A detector is only RUNNING when an enabled configuration exists *and* a
    compatible model is loaded. This table never implies that a detector works:
    it records what an operator asked for. ``validation_status`` stays
    NOT_VALIDATED until a real evaluation exists.
    """
    __tablename__ = "detector_configs"
    __table_args__ = (
        UniqueConstraint("detector_key", "camera_id", "zone_id", name="uq_detector_scope"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    detector_key = Column(String(50), nullable=False, index=True)  # PPE, HELMET, SAFETY_VEST, RESTRICTED_ZONE, PROXIMITY, FALL, FIRE, SMOKE, LEAKAGE, UNSAFE_BEHAVIOR, PERSON
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True, index=True)
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="CASCADE"), nullable=True, index=True)
    is_enabled = Column(Boolean, default=False, nullable=False)
    parameters_json = Column(JSON, nullable=True)
    required_classes_json = Column(JSON, nullable=True)  # Model classes this detector needs
    threshold_source = Column(String(80), default="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION", nullable=False)
    source_reference = Column(String(500), nullable=True)
    validation_status = Column(String(50), default="NOT_VALIDATED", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    camera = relationship("Camera", back_populates="detector_configs")
    zone = relationship("Zone", back_populates="detector_configs")


class SafetyRule(Base):
    """Site safety rule. Values are operator-supplied and always provenance-tagged."""
    __tablename__ = "safety_rules"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    code = Column(String(50), nullable=False, unique=True, index=True)
    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=True, index=True)  # PPE, ZONE, PROXIMITY, FIRE, ACCESS
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    source_reference = Column(String(500), nullable=True)
    validation_status = Column(String(50), default="NOT_VALIDATED", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    zone = relationship("Zone", back_populates="safety_rules")


class OperatingThreshold(Base):
    """A single named operating threshold with explicit provenance.

    Engineering defaults are allowed but are tagged
    ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION and never presented as IGL SOP
    values. No row here is an accuracy or performance measurement.
    """
    __tablename__ = "operating_thresholds"
    __table_args__ = (
        UniqueConstraint("code", "zone_id", "camera_id", name="uq_threshold_scope"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    code = Column(String(80), nullable=False, index=True)
    metric = Column(String(80), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(30), nullable=True)
    comparison = Column(String(10), default="GT", nullable=False)  # GT, GTE, LT, LTE, EQ
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="CASCADE"), nullable=True, index=True)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True, index=True)
    threshold_source = Column(String(80), default="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION", nullable=False)
    source_reference = Column(String(500), nullable=True)
    validation_status = Column(String(50), default="NOT_VALIDATED", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    zone = relationship("Zone", back_populates="operating_thresholds")
    camera = relationship("Camera", back_populates="operating_thresholds")


class EscalationPolicy(Base):
    """When and to whom an unhandled event escalates.

    Policies are operator-supplied. With no policy configured the platform
    reports ESCALATION_NOT_CONFIGURED rather than inventing a chain.
    """
    __tablename__ = "escalation_policies"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(150), nullable=False)
    event_type = Column(String(50), nullable=True, index=True)  # NULL = any event type
    severity = Column(String(20), nullable=True, index=True)  # NULL = any severity
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="CASCADE"), nullable=True, index=True)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True, index=True)
    escalate_after_seconds = Column(Integer, nullable=False, default=900)
    from_role = Column(String(50), nullable=True)
    to_role = Column(String(50), nullable=False)
    escalation_level = Column(Integer, default=1, nullable=False)
    notify_channels_json = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    source_reference = Column(String(500), nullable=True)
    created_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    zone = relationship("Zone", back_populates="escalation_policies")
    camera = relationship("Camera", back_populates="escalation_policies")
    escalations = relationship("EventEscalation", back_populates="policy", cascade="all, delete-orphan")


class EventEscalation(Base):
    """Record of a policy being applied to an event."""
    __tablename__ = "event_escalations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    policy_id = Column(String(36), ForeignKey("escalation_policies.id", ondelete="CASCADE"), nullable=False, index=True)
    escalation_level = Column(Integer, default=1, nullable=False)
    from_role = Column(String(50), nullable=True)
    to_role = Column(String(50), nullable=False)
    reason = Column(Text(), nullable=False)
    triggered_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    event = relationship("Event", back_populates="escalations")
    policy = relationship("EscalationPolicy", back_populates="escalations")


class EventCorrelation(Base):
    """Grouping of repeated related events for one camera and event type.

    Correlation is computed from persisted events only. It never creates an
    event and never invents a count.
    """
    __tablename__ = "event_correlations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    correlation_key = Column(String(200), nullable=False, index=True)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True, index=True)
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    window_seconds = Column(Integer, nullable=False, default=300)
    event_count = Column(Integer, default=1, nullable=False)
    event_ids_json = Column(JSON, nullable=True)
    first_event_at = Column(DateTime(timezone=True), nullable=False)
    last_event_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    camera = relationship("Camera", back_populates="event_correlations")
    zone = relationship("Zone", back_populates="event_correlations")


class NotificationPolicy(Base):
    """Which channel notifies which role for an event class."""
    __tablename__ = "notification_policies"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(150), nullable=False)
    event_type = Column(String(50), nullable=True, index=True)
    severity = Column(String(20), nullable=True, index=True)
    zone_id = Column(String(36), ForeignKey("zones.id", ondelete="CASCADE"), nullable=True, index=True)
    channel = Column(String(50), nullable=False)
    recipient_role = Column(String(50), nullable=False)
    dedup_window_seconds = Column(Integer, default=300, nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)
    source_reference = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    zone = relationship("Zone", back_populates="notification_policies")
