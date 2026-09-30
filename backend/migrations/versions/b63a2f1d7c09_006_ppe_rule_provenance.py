"""Record PPE threshold provenance and validation state."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b63a2f1d7c09"
down_revision: Union[str, None] = "f2c8a6d104be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("ppe_rules", sa.Column("source_reference", sa.String(length=500), nullable=True))
    op.add_column(
        "ppe_rules",
        sa.Column(
            "threshold_source",
            sa.String(length=80),
            nullable=False,
            server_default="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION",
        ),
    )
    op.add_column(
        "ppe_rules",
        sa.Column("validation_status", sa.String(length=50), nullable=False, server_default="NOT_VALIDATED"),
    )
    with op.batch_alter_table("ppe_rules") as batch_op:
        batch_op.alter_column("min_confidence", existing_type=sa.Float(), nullable=True)

def downgrade() -> None:
    connection = op.get_bind()
    has_null_threshold = connection.execute(
        sa.text("SELECT 1 FROM ppe_rules WHERE min_confidence IS NULL LIMIT 1")
    ).first()
    if has_null_threshold:
        raise RuntimeError("Cannot downgrade: PPE rule threshold has no value")
    with op.batch_alter_table("ppe_rules") as batch_op:
        batch_op.alter_column("min_confidence", existing_type=sa.Float(), nullable=False)
    op.drop_column("ppe_rules", "validation_status")
    op.drop_column("ppe_rules", "threshold_source")
    op.drop_column("ppe_rules", "source_reference")
"""Record PPE threshold provenance and validation state."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b63a2f1d7c09"
down_revision: Union[str, None] = "f2c8a6d104be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ppe_rules", sa.Column("source_reference", sa.String(length=500), nullable=True))
    op.add_column(
        "ppe_rules",
        sa.Column(
            "threshold_source",
            sa.String(length=80),
            nullable=False,
            server_default="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION",
        ),
    )
    op.add_column(
        "ppe_rules",
        sa.Column("validation_status", sa.String(length=50), nullable=False, server_default="NOT_VALIDATED"),
    )
    with op.batch_alter_table("ppe_rules") as batch_op:
        batch_op.alter_column("min_confidence", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    has_null_threshold = connection.execute(
        sa.text("SELECT 1 FROM ppe_rules WHERE min_confidence IS NULL LIMIT 1")
    ).first()
    if has_null_threshold:
        raise RuntimeError("Cannot downgrade: PPE rule threshold has no value")
    with op.batch_alter_table("ppe_rules") as batch_op:
        batch_op.alter_column("min_confidence", existing_type=sa.Float(), nullable=False)
    op.drop_column("ppe_rules", "validation_status")
    op.drop_column("ppe_rules", "threshold_source")
    op.drop_column("ppe_rules", "source_reference")