"""Add meeting series recurrence

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-20

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meeting_series", sa.Column("recurrence_rrule", sa.Text(), nullable=True))
    op.add_column(
        "meeting_series",
        sa.Column("recurrence_dtstart", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meeting_series", "recurrence_dtstart")
    op.drop_column("meeting_series", "recurrence_rrule")
