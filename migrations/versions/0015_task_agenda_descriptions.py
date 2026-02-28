"""Add optional descriptions for task and agenda items.

Revision ID: 0015
Revises: 0014
Create Date: 2026-02-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agenda_item") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))

    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("task") as batch:
        batch.drop_column("description")

    with op.batch_alter_table("agenda_item") as batch:
        batch.drop_column("description")
