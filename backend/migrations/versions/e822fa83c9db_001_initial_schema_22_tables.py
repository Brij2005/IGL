"""001_initial_schema_22_tables

Revision ID: e822fa83c9db
Revises: None
Create Date: 2026-09-30 22:00:00.021709

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

try:
    from app.models import Base
except ImportError:
    from backend.app.models import Base


# revision identifiers, used by Alembic.
revision: str = 'e822fa83c9db'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
