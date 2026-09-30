"""Add safety response lifecycles: near-miss status, transition history, honest event metrics.

This revision exists so the response entities can have real, auditable
lifecycles instead of write-only rows:

* ``near_misses`` gains an explicit ``status``/``updated_at``/``closed_at``. A
  near-miss previously had no state at all, so it could not be triaged.
* ``incident_state_transitions`` and ``corrective_action_state_transitions``
  mirror the existing ``event_state_transitions`` table so every allowed state
  change is recorded with actor and reason.
* ``events.confidence`` and ``events.duration_seconds`` become nullable. System
  observations such as a camera failure declare
  ``observation_state='NOT_ASSESSABLE'``; serving them a hard-coded
  ``confidence=1.0`` and ``duration_seconds=0.0`` presented an unmeasured value
  as if it were a model score. Existing rows are preserved unchanged; only the
  nullability of the column changes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4e8f1b3c7a92"
down_revision: Union[str, None] = "9c4b2e7a1d55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEAR_MISS_STATES = ("REPORTED", "UNDER_REVIEW", "CONFIRMED", "DISMISSED", "CLOSED")


def upgrade() -> None:
    op.add_column("near_misses", sa.Column("status", sa.String(length=50), nullable=False, server_default="REPORTED"))
    op.add_column("near_misses", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.add_column("near_misses", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_near_misses_status", "near_misses", ["status"], unique=False)

    op.create_table(
        "incident_state_transitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("previous_state", sa.String(length=50), nullable=False),
        sa.Column("new_state", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incident_state_transitions_incident_id", "incident_state_transitions", ["incident_id"])
    op.create_index("ix_incident_state_transitions_user_id", "incident_state_transitions", ["user_id"])

    op.create_table(
        "near_miss_state_transitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("near_miss_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("previous_state", sa.String(length=50), nullable=False),
        sa.Column("new_state", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["near_miss_id"], ["near_misses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_near_miss_state_transitions_near_miss_id", "near_miss_state_transitions", ["near_miss_id"])
    op.create_index("ix_near_miss_state_transitions_user_id", "near_miss_state_transitions", ["user_id"])

    op.create_table(
        "corrective_action_state_transitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("corrective_action_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("previous_state", sa.String(length=50), nullable=False),
        sa.Column("new_state", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["corrective_action_id"], ["corrective_actions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_corrective_action_state_transitions_corrective_action_id", "corrective_action_state_transitions", ["corrective_action_id"])
    op.create_index("ix_corrective_action_state_transitions_user_id", "corrective_action_state_transitions", ["user_id"])

    # Relax nullability so a system observation can declare NOT_ASSESSABLE
    # instead of carrying a fabricated score. Relaxes before any backfill so
    # the existing rows are never touched.
    with op.batch_alter_table("events") as batch_op:
        batch_op.alter_column("confidence", existing_type=sa.Float(), nullable=True)
        batch_op.alter_column("duration_seconds", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    # A downgraded database must satisfy the original NOT NULL contract.
    op.execute(sa.text("UPDATE events SET confidence = 0.0 WHERE confidence IS NULL"))
    op.execute(sa.text("UPDATE events SET duration_seconds = 0.0 WHERE duration_seconds IS NULL"))

    with op.batch_alter_table("events") as batch_op:
        batch_op.alter_column("duration_seconds", existing_type=sa.Float(), nullable=False)
        batch_op.alter_column("confidence", existing_type=sa.Float(), nullable=False)

    op.drop_index("ix_corrective_action_state_transitions_user_id", table_name="corrective_action_state_transitions")
    op.drop_index("ix_corrective_action_state_transitions_corrective_action_id", table_name="corrective_action_state_transitions")
    op.drop_table("corrective_action_state_transitions")

    op.drop_index("ix_near_miss_state_transitions_user_id", table_name="near_miss_state_transitions")
    op.drop_index("ix_near_miss_state_transitions_near_miss_id", table_name="near_miss_state_transitions")
    op.drop_table("near_miss_state_transitions")

    op.drop_index("ix_incident_state_transitions_user_id", table_name="incident_state_transitions")
    op.drop_index("ix_incident_state_transitions_incident_id", table_name="incident_state_transitions")
    op.drop_table("incident_state_transitions")

    op.drop_index("ix_near_misses_status", table_name="near_misses")
    op.drop_column("near_misses", "closed_at")
    op.drop_column("near_misses", "updated_at")
    op.drop_column("near_misses", "status")
