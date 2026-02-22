"""Add per-series reminder lead time.

Revision ID: 0013
Revises: 0012
Create Date: 2026-02-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("meeting_series") as batch:
        batch.add_column(
            sa.Column("reminder_minutes_before", sa.Integer(), nullable=False, server_default="60")
        )


def downgrade() -> None:
    with op.batch_alter_table("meeting_series") as batch:
        batch.drop_column("reminder_minutes_before")
