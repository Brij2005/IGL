"""Add nullable observed camera health measurements."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c14a2b9f6d31"
down_revision: Union[str, None] = "e822fa83c9db"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("camera_health", sa.Column("measured_fps", sa.Float(), nullable=True))
    op.add_column("camera_health", sa.Column("frame_latency_ms", sa.Float(), nullable=True))
    op.add_column("camera_health", sa.Column("observed_resolution", sa.String(length=20), nullable=True))
    op.add_column("camera_health", sa.Column("dropped_frames", sa.Integer(), nullable=True))
    op.add_column("camera_health", sa.Column("brightness_score", sa.Float(), nullable=True))
    op.add_column("camera_health", sa.Column("sharpness_score", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("camera_health", "sharpness_score")
    op.drop_column("camera_health", "brightness_score")
    op.drop_column("camera_health", "dropped_frames")
    op.drop_column("camera_health", "observed_resolution")
    op.drop_column("camera_health", "frame_latency_ms")
    op.drop_column("camera_health", "measured_fps")