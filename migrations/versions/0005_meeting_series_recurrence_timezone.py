"""Add meeting series recurrence timezone

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-20

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_series",
        sa.Column("recurrence_timezone", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meeting_series", "recurrence_timezone")
