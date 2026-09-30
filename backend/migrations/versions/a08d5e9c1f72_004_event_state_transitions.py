"""Add durable event workflow transition history."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a08d5e9c1f72"
down_revision: Union[str, None] = "d71e3f0a2c44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_state_transitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("previous_state", sa.String(length=50), nullable=False),
        sa.Column("new_state", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_state_transitions_event_id", "event_state_transitions", ["event_id"])
    op.create_index("ix_event_state_transitions_user_id", "event_state_transitions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_event_state_transitions_user_id", table_name="event_state_transitions")
    op.drop_index("ix_event_state_transitions_event_id", table_name="event_state_transitions")
    op.drop_table("event_state_transitions")
