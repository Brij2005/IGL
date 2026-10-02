"""Persist the provider acknowledgement ID for delivered notifications."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7d2e6c3a991"
down_revision: Union[str, None] = "cc021fcc26b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("provider_message_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "provider_message_id")
