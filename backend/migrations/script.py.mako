"""Generic Alembic revision template for ClimateIQ."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # type: ignore[attr-defined]
import sqlalchemy as sa  # type: ignore[attr-defined]


revision = ${repr(revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
