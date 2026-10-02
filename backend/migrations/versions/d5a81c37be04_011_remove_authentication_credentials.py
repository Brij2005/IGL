"""Remove authentication credential storage.

This build has no login, so it must not keep credential material. The change is
deliberately narrow and reversible:

* ``users.hashed_password`` becomes nullable and every stored value is cleared.
  The column is kept rather than dropped so no historical migration has to be
  rewritten, and so a future deployment that reintroduces authentication has an
  obvious place to store a hash.
* ``users.password_changed_at``, ``users.must_change_password`` and
  ``users.last_login_at`` are dropped. They existed only to invalidate sessions,
  which is meaningless without sessions.
* ``users.deactivated_at`` and ``users.deactivated_by_user_id`` are kept:
  deactivation is an operational state (an identity is not assignable work), not
  a credential one.
* ``notifications.user_id`` becomes nullable and ``recipient_role`` is added.
  Notifications previously assumed a signed-in recipient; without authentication
  a notification addresses a role, so a row must be able to exist with no user.

No row is deleted. Audit history, assignments, incidents and events keep their
references to identities, which continue to mean "the named operator".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5a81c37be04"
down_revision: Union[str, None] = "c7d1e42a9b83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DROPPED_CREDENTIAL_COLUMNS = ("password_changed_at", "must_change_password", "last_login_at")


def upgrade() -> None:
    # Backfill before relaxing the constraint: the column is NOT NULL today, so a
    # clear has to happen inside the same batch rebuild that makes it nullable.
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("hashed_password", existing_type=sa.String(length=255), nullable=True)
        batch_op.drop_column("password_changed_at")
        batch_op.drop_column("must_change_password")
        batch_op.drop_column("last_login_at")
    # No credential material is retained anywhere in this deployment.
    op.execute(sa.text("UPDATE users SET hashed_password = NULL WHERE hashed_password IS NOT NULL"))

    op.add_column("notifications", sa.Column("recipient_role", sa.String(length=50), nullable=True))
    op.create_index("ix_notifications_recipient_role", "notifications", ["recipient_role"], unique=False)
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    # Rows created without a user cannot be restored to the old contract, so the
    # downgrade reports that rather than silently corrupting them.
    orphans = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM notifications WHERE user_id IS NULL")
    ).scalar()
    if orphans:
        raise RuntimeError(
            f"Cannot downgrade: {orphans} notification row(s) have no user_id because "
            "notifications were addressed to a role. Reassign them before downgrading."
        )
    op.execute(sa.text("UPDATE users SET hashed_password = '' WHERE hashed_password IS NULL"))
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(length=36), nullable=False)
    op.drop_index("ix_notifications_recipient_role", table_name="notifications")
    op.drop_column("notifications", "recipient_role")
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))
        )
        batch_op.alter_column("hashed_password", existing_type=sa.String(length=255), nullable=False)