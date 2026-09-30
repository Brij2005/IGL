"""Allow camera health to stay unmeasured instead of defaulting to 0.0.

``camera_health.fps`` and ``camera_health.latency_ms`` were NOT NULL with a
``0.0`` default, so a camera that had never produced a frame reported a
plausible-looking measured value of zero. They are superseded by the nullable
``measured_fps`` and ``frame_latency_ms`` columns added in revision
c14a2b9f6d31. This migration only relaxes nullability and backfills existing
placeholder zeros to NULL so the columns no longer assert a measurement.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c4b2e7a1d55"
down_revision: Union[str, None] = "b63a2f1d7c09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UNMEASURED_PLACEHOLDER_COLUMNS = ("fps", "latency_ms")


def upgrade() -> None:
    # Relax nullability first; the backfill below could not write NULL while
    # the columns are still declared NOT NULL.
    with op.batch_alter_table("camera_health") as batch_op:
        batch_op.alter_column("fps", existing_type=sa.Float(), nullable=True)
        batch_op.alter_column("latency_ms", existing_type=sa.Float(), nullable=True)
    op.execute(
        sa.text(
            "UPDATE camera_health SET fps = NULL, latency_ms = NULL "
            "WHERE (fps = 0.0 OR fps IS NULL) AND (latency_ms = 0.0 OR latency_ms IS NULL)"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE camera_health SET fps = 0.0, latency_ms = 0.0 WHERE fps IS NULL OR latency_ms IS NULL")
    )
    with op.batch_alter_table("camera_health") as batch_op:
        batch_op.alter_column("latency_ms", existing_type=sa.Float(), nullable=False)
        batch_op.alter_column("fps", existing_type=sa.Float(), nullable=False)
