"""Allow image quality to remain absent until measured."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d71e3f0a2c44"
down_revision: Union[str, None] = "c14a2b9f6d31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("camera_health") as batch_op:
        batch_op.alter_column(
            "image_quality_score",
            existing_type=sa.Float(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("camera_health") as batch_op:
        batch_op.alter_column(
            "image_quality_score",
            existing_type=sa.Float(),
            nullable=False,
        )