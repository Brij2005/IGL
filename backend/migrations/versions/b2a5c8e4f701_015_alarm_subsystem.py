"""Add the alarm subsystem: alarms and their audited state transitions."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2a5c8e4f701"
down_revision: Union[str, None] = "e7d2e6c3a991"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alarms",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_id", sa.String(length=36), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("camera_id", sa.String(length=36), sa.ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True),
        sa.Column("zone_id", sa.String(length=36), sa.ForeignKey("zones.id", ondelete="SET NULL"), nullable=True),
        sa.Column("track_id", sa.String(length=36), sa.ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("incident_id", sa.String(length=36), sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="MEDIUM"),
        sa.Column("state", sa.String(length=30), nullable=False, server_default="ACTIVE"),
        sa.Column("detector_key", sa.String(length=50), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("model_version", sa.String(length=50), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=True),
        sa.Column("raised_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("suppression_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("physical_state", sa.String(length=50), nullable=False, server_default="PHYSICAL_ALARM_NOT_CONFIGURED"),
        sa.Column("physical_result", sa.String(length=255), nullable=True),
        sa.Column("physical_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("physical_cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alarms_event_id", "alarms", ["event_id"])
    op.create_index("ix_alarms_camera_id", "alarms", ["camera_id"])
    op.create_index("ix_alarms_zone_id", "alarms", ["zone_id"])
    op.create_index("ix_alarms_track_id", "alarms", ["track_id"])
    op.create_index("ix_alarms_incident_id", "alarms", ["incident_id"])
    op.create_index("ix_alarms_severity", "alarms", ["severity"])
    op.create_index("ix_alarms_state", "alarms", ["state"])
    op.create_index("ix_alarms_detector_key", "alarms", ["detector_key"])
    op.create_index("ix_alarms_acknowledged_by_user_id", "alarms", ["acknowledged_by_user_id"])
    op.create_index("ix_alarms_cooldown_until", "alarms", ["cooldown_until"])
    op.create_index("ix_alarms_expires_at", "alarms", ["expires_at"])

    op.create_table(
        "alarm_state_transitions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("alarm_id", sa.String(length=36), sa.ForeignKey("alarms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("previous_state", sa.String(length=30), nullable=True),
        sa.Column("new_state", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alarm_state_transitions_alarm_id", "alarm_state_transitions", ["alarm_id"])
    op.create_index("ix_alarm_state_transitions_user_id", "alarm_state_transitions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_alarm_state_transitions_user_id", table_name="alarm_state_transitions")
    op.drop_index("ix_alarm_state_transitions_alarm_id", table_name="alarm_state_transitions")
    op.drop_table("alarm_state_transitions")
    op.drop_index("ix_alarms_expires_at", table_name="alarms")
    op.drop_index("ix_alarms_cooldown_until", table_name="alarms")
    op.drop_index("ix_alarms_acknowledged_by_user_id", table_name="alarms")
    op.drop_index("ix_alarms_detector_key", table_name="alarms")
    op.drop_index("ix_alarms_state", table_name="alarms")
    op.drop_index("ix_alarms_severity", table_name="alarms")
    op.drop_index("ix_alarms_incident_id", table_name="alarms")
    op.drop_index("ix_alarms_track_id", table_name="alarms")
    op.drop_index("ix_alarms_zone_id", table_name="alarms")
    op.drop_index("ix_alarms_camera_id", table_name="alarms")
    op.drop_index("ix_alarms_event_id", table_name="alarms")
    op.drop_table("alarms")
