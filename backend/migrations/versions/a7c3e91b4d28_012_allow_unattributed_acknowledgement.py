"""Allow an unattributed acknowledgement.

Nothing in this build authenticates a request, so the acting identity for an
acknowledgement is normally unknown and the workflow engine already writes
``user_id = None``. The column was still ``NOT NULL``, which made the honest
path impossible: the platform could only record an acknowledgement by crediting
it to some identity that may never have acted.

This migration makes ``acknowledgements.user_id`` nullable and changes the
foreign key's delete behaviour from ``CASCADE`` to ``SET NULL``.

``SET NULL`` matters as much as the nullability. With ``CASCADE``, deleting an
identity would silently delete the record that an event was handled, destroying
the only evidence that someone dealt with a safety event. ``SET NULL`` keeps the
acknowledgement and leaves it visibly unattributed, which is the truthful
outcome.

``event_state_transitions.user_id`` was already nullable, so this change only
brings the two audit tables into agreement.

No row is deleted or rewritten.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c3e91b4d28"
down_revision: Union[str, None] = "d5a81c37be04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table(
        "acknowledgements",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch_op:
        batch_op.drop_constraint("fk_acknowledgements_user_id_users", type_="foreignkey")
        batch_op.alter_column("user_id", existing_type=sa.String(length=36), nullable=True)
        batch_op.create_foreign_key(
            "fk_acknowledgements_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Rows with no actor cannot satisfy the original NOT NULL constraint, so the
    # downgrade states that rather than silently deleting acknowledgement history.
    connection = op.get_bind()
    unattributed = connection.execute(
        sa.text("SELECT COUNT(*) FROM acknowledgements WHERE user_id IS NULL")
    ).scalar_one()
    if unattributed:
        raise RuntimeError(
            "Cannot restore NOT NULL on acknowledgements.user_id: "
            f"{unattributed} row(s) have no attributed actor. Re-attributing them "
            "is a manual decision and must not be done automatically."
        )
    with op.batch_alter_table(
        "acknowledgements",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch_op:
        batch_op.drop_constraint("fk_acknowledgements_user_id_users", type_="foreignkey")
        batch_op.alter_column("user_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.create_foreign_key(
            "fk_acknowledgements_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )

