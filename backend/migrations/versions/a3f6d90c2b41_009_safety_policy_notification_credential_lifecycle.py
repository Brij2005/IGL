"""Add safety policy configuration, notification delivery bookkeeping and credential lifecycle.

Scope of this revision:

* ``users``: credential lifecycle columns. ``password_changed_at`` lets the API
  reject tokens minted before a password change, so a reset invalidates every
  outstanding session. Existing rows are stamped with the migration time, which
  is a truthful "at or before now" boundary rather than an invented history.
* ``camera_health``: ``is_frozen`` / ``is_black`` were NOT NULL defaults of
  ``False``, so a camera that had never produced a frame asserted "not frozen"
  and "not black". They become nullable and unobserved placeholder values are
  backfilled to NULL.
* ``notifications``: delivery bookkeeping (``retry_count``, ``max_attempts``,
  ``next_attempt_at``, ``last_attempt_at``, ``dedup_key``, ``provider``,
  ``payload_summary``). Legacy ``PENDING`` rows become ``QUEUED`` because
  ``PENDING`` implies an in-flight delivery attempt that was never recorded.
* ``event_evidence``: ``size_bytes``, ``camera_id``, ``retention_policy`` and
  ``retention_expires_at`` for evidence custody and retention handling.
* New tables ``detector_configs``, ``safety_rules``, ``operating_thresholds``,
  ``escalation_policies``, ``event_escalations``, ``event_correlations`` and
  ``notification_policies``. Every threshold-bearing row carries
  ``threshold_source`` and ``validation_status`` so an engineering default can
  never be read as an IGL-validated operating value.

All operations are reversible; no data is deleted on upgrade.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3f6d90c2b41"
down_revision: Union[str, None] = "4e8f1b3c7a92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # users: credential lifecycle
    # ------------------------------------------------------------------
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The self-referencing foreign key needs batch mode: SQLite cannot ALTER a
    # constraint into an existing table, only copy-and-move.
    op.add_column(
        "users",
        sa.Column("deactivated_by_user_id", sa.String(length=36), nullable=True),
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.create_foreign_key(
            "fk_users_deactivated_by_user_id",
            "users",
            ["deactivated_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_users_deactivated_by_user_id", "users", ["deactivated_by_user_id"], unique=False)
    # Stamp existing credentials with the migration instant, then enforce NOT
    # NULL. Order matters: the NOT NULL batch rebuild cannot succeed while any
    # row still holds NULL.
    op.execute(sa.text("UPDATE users SET password_changed_at = CURRENT_TIMESTAMP WHERE password_changed_at IS NULL"))
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "password_changed_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    # ------------------------------------------------------------------
    # camera_health: no asserted image verdict without an observed frame
    # ------------------------------------------------------------------
    with op.batch_alter_table("camera_health") as batch_op:
        batch_op.alter_column("is_frozen", existing_type=sa.Boolean(), nullable=True)
        batch_op.alter_column("is_black", existing_type=sa.Boolean(), nullable=True)
    # A row that never recorded a frame and never recorded telemetry is an
    # unobserved camera, so the placeholder False values must not survive.
    op.execute(
        sa.text(
            "UPDATE camera_health SET is_frozen = NULL, is_black = NULL "
            "WHERE last_frame_timestamp IS NULL AND fps IS NULL AND latency_ms IS NULL "
            "AND measured_fps IS NULL AND frame_latency_ms IS NULL"
        )
    )
    op.execute(sa.text("UPDATE camera_health SET status = 'NEVER_EVALUATED' WHERE status = 'UNKNOWN'"))
    op.execute(sa.text("UPDATE camera_health SET inference_status = 'NOT_RUNNING' WHERE inference_status = 'IDLE'"))
    with op.batch_alter_table("camera_health") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=50),
            nullable=False,
            server_default=sa.text("'NEVER_EVALUATED'"),
        )
        batch_op.alter_column(
            "inference_status",
            existing_type=sa.String(length=50),
            nullable=False,
            server_default=sa.text("'NOT_RUNNING'"),
        )

    # ------------------------------------------------------------------
    # notifications: auditable delivery state
    # ------------------------------------------------------------------
    op.add_column(
        "notifications",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "notifications",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
    )
    op.add_column(
        "notifications",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("dedup_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("payload_summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_notifications_dedup_key", "notifications", ["dedup_key"], unique=False)
    # The dispatch queue filters on status, so the retry sweeper needs the index.
    op.create_index("ix_notifications_status", "notifications", ["status"], unique=False)
    op.execute(sa.text("UPDATE notifications SET status = 'QUEUED' WHERE status = 'PENDING'"))

    # ------------------------------------------------------------------
    # event_evidence: custody and retention
    # ------------------------------------------------------------------
    op.add_column("event_evidence", sa.Column("size_bytes", sa.Integer(), nullable=True))
    op.add_column("event_evidence", sa.Column("camera_id", sa.String(length=36), nullable=True))
    with op.batch_alter_table("event_evidence") as batch_op:
        batch_op.create_foreign_key(
            "fk_event_evidence_camera_id",
            "cameras",
            ["camera_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.add_column("event_evidence", sa.Column("retention_policy", sa.String(length=50), nullable=True))
    op.add_column(
        "event_evidence",
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_event_evidence_camera_id", "event_evidence", ["camera_id"], unique=False)
    op.create_index(
        "ix_event_evidence_retention_expires_at", "event_evidence", ["retention_expires_at"], unique=False
    )

    # ------------------------------------------------------------------
    # detector_configs
    # ------------------------------------------------------------------
    op.create_table(
        "detector_configs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("detector_key", sa.String(length=50), nullable=False),
        sa.Column("camera_id", sa.String(length=36), sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True),
        sa.Column("zone_id", sa.String(length=36), sa.ForeignKey("zones.id", ondelete="CASCADE"), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("parameters_json", sa.JSON(), nullable=True),
        sa.Column("required_classes_json", sa.JSON(), nullable=True),
        sa.Column(
            "threshold_source",
            sa.String(length=80),
            nullable=False,
            server_default=sa.text("'ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION'"),
        ),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("validation_status", sa.String(length=50), nullable=False, server_default=sa.text("'NOT_VALIDATED'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("detector_key", "camera_id", "zone_id", name="uq_detector_scope"),
    )
    op.create_index("ix_detector_configs_detector_key", "detector_configs", ["detector_key"], unique=False)
    op.create_index("ix_detector_configs_camera_id", "detector_configs", ["camera_id"], unique=False)
    op.create_index("ix_detector_configs_zone_id", "detector_configs", ["zone_id"], unique=False)

    # ------------------------------------------------------------------
    # safety_rules
    # ------------------------------------------------------------------
    op.create_table(
        "safety_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("zone_id", sa.String(length=36), sa.ForeignKey("zones.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("validation_status", sa.String(length=50), nullable=False, server_default=sa.text("'NOT_VALIDATED'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_safety_rules_code", "safety_rules", ["code"], unique=True)
    op.create_index("ix_safety_rules_category", "safety_rules", ["category"], unique=False)
    op.create_index("ix_safety_rules_zone_id", "safety_rules", ["zone_id"], unique=False)

    # ------------------------------------------------------------------
    # operating_thresholds
    # ------------------------------------------------------------------
    op.create_table(
        "operating_thresholds",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("metric", sa.String(length=80), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("comparison", sa.String(length=10), nullable=False, server_default=sa.text("'GT'")),
        sa.Column("zone_id", sa.String(length=36), sa.ForeignKey("zones.id", ondelete="CASCADE"), nullable=True),
        sa.Column("camera_id", sa.String(length=36), sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True),
        sa.Column(
            "threshold_source",
            sa.String(length=80),
            nullable=False,
            server_default=sa.text("'ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION'"),
        ),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("validation_status", sa.String(length=50), nullable=False, server_default=sa.text("'NOT_VALIDATED'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("code", "zone_id", "camera_id", name="uq_threshold_scope"),
    )
    op.create_index("ix_operating_thresholds_code", "operating_thresholds", ["code"], unique=False)
    op.create_index("ix_operating_thresholds_zone_id", "operating_thresholds", ["zone_id"], unique=False)
    op.create_index("ix_operating_thresholds_camera_id", "operating_thresholds", ["camera_id"], unique=False)

    # ------------------------------------------------------------------
    # escalation_policies / event_escalations
    # ------------------------------------------------------------------
    op.create_table(
        "escalation_policies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("zone_id", sa.String(length=36), sa.ForeignKey("zones.id", ondelete="CASCADE"), nullable=True),
        sa.Column("camera_id", sa.String(length=36), sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True),
        sa.Column("escalate_after_seconds", sa.Integer(), nullable=False, server_default=sa.text("900")),
        sa.Column("from_role", sa.String(length=50), nullable=True),
        sa.Column("to_role", sa.String(length=50), nullable=False),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("notify_channels_json", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_escalation_policies_event_type", "escalation_policies", ["event_type"], unique=False)
    op.create_index("ix_escalation_policies_severity", "escalation_policies", ["severity"], unique=False)
    op.create_index("ix_escalation_policies_zone_id", "escalation_policies", ["zone_id"], unique=False)
    op.create_index("ix_escalation_policies_camera_id", "escalation_policies", ["camera_id"], unique=False)

    op.create_table(
        "event_escalations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_id", sa.String(length=36), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "policy_id",
            sa.String(length=36),
            sa.ForeignKey("escalation_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("from_role", sa.String(length=50), nullable=True),
        sa.Column("to_role", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "acknowledged_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_event_escalations_event_id", "event_escalations", ["event_id"], unique=False)
    op.create_index("ix_event_escalations_policy_id", "event_escalations", ["policy_id"], unique=False)

    # ------------------------------------------------------------------
    # event_correlations
    # ------------------------------------------------------------------
    op.create_table(
        "event_correlations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("correlation_key", sa.String(length=200), nullable=False),
        sa.Column("camera_id", sa.String(length=36), sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True),
        sa.Column("zone_id", sa.String(length=36), sa.ForeignKey("zones.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("event_ids_json", sa.JSON(), nullable=True),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_event_correlations_correlation_key", "event_correlations", ["correlation_key"], unique=False)
    op.create_index("ix_event_correlations_camera_id", "event_correlations", ["camera_id"], unique=False)
    op.create_index("ix_event_correlations_zone_id", "event_correlations", ["zone_id"], unique=False)
    op.create_index("ix_event_correlations_event_type", "event_correlations", ["event_type"], unique=False)

    # ------------------------------------------------------------------
    # notification_policies
    # ------------------------------------------------------------------
    op.create_table(
        "notification_policies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("zone_id", sa.String(length=36), sa.ForeignKey("zones.id", ondelete="CASCADE"), nullable=True),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("recipient_role", sa.String(length=50), nullable=False),
        sa.Column("dedup_window_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_notification_policies_event_type", "notification_policies", ["event_type"], unique=False)
    op.create_index("ix_notification_policies_severity", "notification_policies", ["severity"], unique=False)
    op.create_index("ix_notification_policies_zone_id", "notification_policies", ["zone_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notification_policies_zone_id", table_name="notification_policies")
    op.drop_index("ix_notification_policies_severity", table_name="notification_policies")
    op.drop_index("ix_notification_policies_event_type", table_name="notification_policies")
    op.drop_table("notification_policies")

    op.drop_index("ix_event_correlations_event_type", table_name="event_correlations")
    op.drop_index("ix_event_correlations_zone_id", table_name="event_correlations")
    op.drop_index("ix_event_correlations_camera_id", table_name="event_correlations")
    op.drop_index("ix_event_correlations_correlation_key", table_name="event_correlations")
    op.drop_table("event_correlations")

    op.drop_index("ix_event_escalations_policy_id", table_name="event_escalations")
    op.drop_index("ix_event_escalations_event_id", table_name="event_escalations")
    op.drop_table("event_escalations")

    op.drop_index("ix_escalation_policies_camera_id", table_name="escalation_policies")
    op.drop_index("ix_escalation_policies_zone_id", table_name="escalation_policies")
    op.drop_index("ix_escalation_policies_severity", table_name="escalation_policies")
    op.drop_index("ix_escalation_policies_event_type", table_name="escalation_policies")
    op.drop_table("escalation_policies")

    op.drop_index("ix_operating_thresholds_camera_id", table_name="operating_thresholds")
    op.drop_index("ix_operating_thresholds_zone_id", table_name="operating_thresholds")
    op.drop_index("ix_operating_thresholds_code", table_name="operating_thresholds")
    op.drop_table("operating_thresholds")

    op.drop_index("ix_safety_rules_zone_id", table_name="safety_rules")
    op.drop_index("ix_safety_rules_category", table_name="safety_rules")
    op.drop_index("ix_safety_rules_code", table_name="safety_rules")
    op.drop_table("safety_rules")

    op.drop_index("ix_detector_configs_zone_id", table_name="detector_configs")
    op.drop_index("ix_detector_configs_camera_id", table_name="detector_configs")
    op.drop_index("ix_detector_configs_detector_key", table_name="detector_configs")
    op.drop_table("detector_configs")

    op.drop_index("ix_event_evidence_retention_expires_at", table_name="event_evidence")
    op.drop_index("ix_event_evidence_camera_id", table_name="event_evidence")
    with op.batch_alter_table("event_evidence") as batch_op:
        batch_op.drop_constraint("fk_event_evidence_camera_id", type_="foreignkey")
        batch_op.drop_column("retention_expires_at")
        batch_op.drop_column("retention_policy")
        batch_op.drop_column("camera_id")
        batch_op.drop_column("size_bytes")

    op.drop_index("ix_notifications_dedup_key", table_name="notifications")
    op.drop_index("ix_notifications_status", table_name="notifications")
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_column("payload_summary")
        batch_op.drop_column("provider")
        batch_op.drop_column("dedup_key")
        batch_op.drop_column("last_attempt_at")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("retry_count")
    op.execute(sa.text("UPDATE notifications SET status = 'PENDING' WHERE status = 'QUEUED'"))

    # Backfill first: the NOT NULL batch rebuild copies existing rows and would
    # fail on any NULL it still has to copy.
    op.execute(
        sa.text("UPDATE camera_health SET is_frozen = 0, is_black = 0 WHERE is_frozen IS NULL OR is_black IS NULL")
    )
    op.execute(sa.text("UPDATE camera_health SET status = 'UNKNOWN' WHERE status = 'NEVER_EVALUATED'"))
    op.execute(sa.text("UPDATE camera_health SET inference_status = 'IDLE' WHERE inference_status = 'NOT_RUNNING'"))
    with op.batch_alter_table("camera_health") as batch_op:
        batch_op.alter_column(
            "inference_status",
            existing_type=sa.String(length=50),
            nullable=False,
            server_default=sa.text("'IDLE'"),
        )
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=50),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        )
        batch_op.alter_column("is_black", existing_type=sa.Boolean(), nullable=False, server_default=sa.false())
        batch_op.alter_column("is_frozen", existing_type=sa.Boolean(), nullable=False, server_default=sa.false())

    op.drop_index("ix_users_deactivated_by_user_id", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_deactivated_by_user_id", type_="foreignkey")
        batch_op.drop_column("deactivated_by_user_id")
        batch_op.drop_column("deactivated_at")
        batch_op.drop_column("last_login_at")
        batch_op.drop_column("must_change_password")
        batch_op.drop_column("password_changed_at")
