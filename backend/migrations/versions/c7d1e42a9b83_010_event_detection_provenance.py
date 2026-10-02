"""Record how each safety event was produced.

An event without provenance cannot be audited, and an audit that cannot explain
where a verdict came from is indistinguishable from a fabricated one. This
revision adds the columns the runtime already needs:

* ``detector_key`` - which configured detector produced the observation.
* ``verification_state`` - the temporal verifier's own verdict
  (DETECTED, PERSISTED, VERIFIED, INVALIDATED, NOT_ASSESSABLE). It stays NULL
  when no temporal verification ran, which is different from claiming the event
  was verified.
* ``temporal_observations`` / ``temporal_duration_seconds`` - the counted
  observations and the measured span behind that verdict. NULL until measured.
* ``threshold_source`` / ``source_reference`` - whether the thresholds that
  produced the event were operator-configured or engineering defaults still
  pending IGL validation.
* ``model_name`` / ``model_weights_checksum`` - which artifact scored the frame.
  The checksum is NULL when the weights could not be hashed, never a stand-in.
* ``provenance_json`` - the exact reason string and configuration references.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d1e42a9b83"
down_revision: Union[str, None] = "a3f6d90c2b41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PROVENANCE_COLUMNS = (
    "detector_key",
    "verification_state",
    "temporal_observations",
    "temporal_duration_seconds",
    "threshold_source",
    "source_reference",
    "model_name",
    "model_weights_checksum",
    "provenance_json",
)


def upgrade() -> None:
    for column in (
        sa.Column("detector_key", sa.String(length=50), nullable=True),
        sa.Column("verification_state", sa.String(length=40), nullable=True),
        sa.Column("temporal_observations", sa.Integer(), nullable=True),
        sa.Column("temporal_duration_seconds", sa.Float(), nullable=True),
        sa.Column("threshold_source", sa.String(length=80), nullable=True),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("model_weights_checksum", sa.String(length=64), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=True),
    ):
        op.add_column("events", column)
    op.create_index("ix_events_detector_key", "events", ["detector_key"], unique=False)
    # An open event awaiting verification is the worker's query surface.
    op.create_index("ix_events_verification_state", "events", ["verification_state"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_events_verification_state", table_name="events")
    op.drop_index("ix_events_detector_key", table_name="events")
    with op.batch_alter_table("events") as batch_op:
        for name in reversed(PROVENANCE_COLUMNS):
            batch_op.drop_column(name)