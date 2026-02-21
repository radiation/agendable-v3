"""Move tasks to meeting occurrence

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-21

"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data migration: map any existing series-level tasks onto the latest
    # scheduled occurrence for that series (or create a placeholder occurrence
    # if none exist).
    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("occurrence_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_index("ix_task_occurrence_id", ["occurrence_id"], unique=False)
        batch.create_foreign_key(
            "fk_task_occurrence",
            "meeting_occurrence",
            ["occurrence_id"],
            ["id"],
        )

    bind = op.get_bind()

    task = sa.table(
        "task",
        sa.column("series_id", sa.Uuid(as_uuid=True)),
        sa.column("occurrence_id", sa.Uuid(as_uuid=True)),
    )
    meeting_occurrence = sa.table(
        "meeting_occurrence",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("series_id", sa.Uuid(as_uuid=True)),
        sa.column("scheduled_at", sa.DateTime(timezone=True)),
        sa.column("notes", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    latest_occurrence_id = (
        sa.select(meeting_occurrence.c.id)
        .where(meeting_occurrence.c.series_id == task.c.series_id)
        .order_by(meeting_occurrence.c.scheduled_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    bind.execute(sa.update(task).values(occurrence_id=latest_occurrence_id))

    series_ids_missing_occurrence = list(
        bind.execute(
            sa.select(sa.distinct(task.c.series_id)).where(task.c.occurrence_id.is_(None))
        ).scalars()
    )
    now = datetime.now(UTC)
    for series_id in series_ids_missing_occurrence:
        new_occurrence_id = uuid.uuid4()
        bind.execute(
            sa.insert(meeting_occurrence).values(
                id=new_occurrence_id,
                series_id=series_id,
                scheduled_at=now,
                notes="",
                created_at=now,
            )
        )
        bind.execute(
            sa.update(task)
            .where(task.c.series_id == series_id)
            .values(occurrence_id=new_occurrence_id)
        )

    with op.batch_alter_table("task") as batch:
        batch.drop_constraint("fk_task_series", type_="foreignkey")
        batch.drop_index("ix_task_series_id")
        batch.drop_column("series_id")

        batch.alter_column("occurrence_id", existing_type=sa.Uuid(as_uuid=True), nullable=False)


def downgrade() -> None:
    # Best-effort reversal: tasks are assigned back to the series of their occurrence.
    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("series_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_index("ix_task_series_id", ["series_id"], unique=False)
        batch.create_foreign_key(
            "fk_task_series",
            "meeting_series",
            ["series_id"],
            ["id"],
        )

    bind = op.get_bind()

    task = sa.table(
        "task",
        sa.column("series_id", sa.Uuid(as_uuid=True)),
        sa.column("occurrence_id", sa.Uuid(as_uuid=True)),
    )
    meeting_occurrence = sa.table(
        "meeting_occurrence",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("series_id", sa.Uuid(as_uuid=True)),
    )

    series_id_for_occurrence = (
        sa.select(meeting_occurrence.c.series_id)
        .where(meeting_occurrence.c.id == task.c.occurrence_id)
        .limit(1)
        .scalar_subquery()
    )
    bind.execute(sa.update(task).values(series_id=series_id_for_occurrence))

    with op.batch_alter_table("task") as batch:
        batch.drop_constraint("fk_task_occurrence", type_="foreignkey")
        batch.drop_index("ix_task_occurrence_id")
        batch.drop_column("occurrence_id")
        batch.alter_column("series_id", existing_type=sa.Uuid(as_uuid=True), nullable=False)
