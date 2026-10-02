"""Restore credential lifecycle metadata for authenticated deployments.

Revision ID: cc021fcc26b1
Revises: a7c3e91b4d28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cc021fcc26b1"
down_revision: Union[str, None] = "a7c3e91b4d28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "password_changed_at")
