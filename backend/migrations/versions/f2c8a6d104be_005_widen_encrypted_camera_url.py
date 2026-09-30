"""Allow for authenticated-encryption expansion of camera URLs."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2c8a6d104be"
down_revision: Union[str, None] = "a08d5e9c1f72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("cameras") as batch_op:
        batch_op.alter_column(
            "stream_url",
            existing_type=sa.String(length=500),
            type_=sa.String(length=1200),
            existing_nullable=False,
        )


def downgrade() -> None:
    connection = op.get_bind()
    has_long_values = connection.execute(
        sa.text("SELECT 1 FROM cameras WHERE length(stream_url) > 500 LIMIT 1")
    ).first()
    if has_long_values:
        raise RuntimeError("Cannot downgrade: a camera URL would be truncated")
    with op.batch_alter_table("cameras") as batch_op:
        batch_op.alter_column(
            "stream_url",
            existing_type=sa.String(length=1200),
            type_=sa.String(length=500),
            existing_nullable=False,
        )