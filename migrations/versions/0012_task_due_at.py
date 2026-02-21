"""Add task due_at.

Revision ID: 0012
Revises: 0011
Create Date: 2026-02-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_task_due_at", ["due_at"], unique=False)

    bind = op.get_bind()

    task = sa.table(
        "task",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("occurrence_id", sa.Uuid(as_uuid=True)),
        sa.column("due_at", sa.DateTime(timezone=True)),
    )
    occurrence = sa.table(
        "meeting_occurrence",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("scheduled_at", sa.DateTime(timezone=True)),
    )

    due_for_occurrence = (
        sa.select(occurrence.c.scheduled_at)
        .where(occurrence.c.id == task.c.occurrence_id)
        .limit(1)
        .scalar_subquery()
    )
    bind.execute(sa.update(task).values(due_at=due_for_occurrence))

    with op.batch_alter_table("task") as batch:
        batch.alter_column("due_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("task") as batch:
        batch.drop_index("ix_task_due_at")
        batch.drop_column("due_at")
