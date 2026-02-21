"""Add task assignee and occurrence attendees.

Revision ID: 0009
Revises: 0008
Create Date: 2026-02-21
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_occurrence_attendee",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("occurrence_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["occurrence_id"], ["meeting_occurrence.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "occurrence_id",
            "user_id",
            name="uq_meeting_occurrence_attendee_occurrence_user",
        ),
    )
    op.create_index(
        "ix_meeting_occurrence_attendee_occurrence_id",
        "meeting_occurrence_attendee",
        ["occurrence_id"],
        unique=False,
    )
    op.create_index(
        "ix_meeting_occurrence_attendee_user_id",
        "meeting_occurrence_attendee",
        ["user_id"],
        unique=False,
    )

    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("assigned_user_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_index("ix_task_assigned_user_id", ["assigned_user_id"], unique=False)
        batch.create_foreign_key(
            "fk_task_assigned_user",
            "users",
            ["assigned_user_id"],
            ["id"],
        )

    bind = op.get_bind()

    task = sa.table(
        "task",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("occurrence_id", sa.Uuid(as_uuid=True)),
        sa.column("assigned_user_id", sa.Uuid(as_uuid=True)),
    )
    meeting_occurrence = sa.table(
        "meeting_occurrence",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("series_id", sa.Uuid(as_uuid=True)),
    )
    meeting_series = sa.table(
        "meeting_series",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("owner_user_id", sa.Uuid(as_uuid=True)),
    )
    attendee = sa.table(
        "meeting_occurrence_attendee",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("occurrence_id", sa.Uuid(as_uuid=True)),
        sa.column("user_id", sa.Uuid(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    owner_for_occurrence = (
        sa.select(meeting_series.c.owner_user_id)
        .select_from(
            meeting_occurrence.join(
                meeting_series, meeting_occurrence.c.series_id == meeting_series.c.id
            )
        )
        .where(meeting_occurrence.c.id == task.c.occurrence_id)
        .limit(1)
        .scalar_subquery()
    )
    bind.execute(sa.update(task).values(assigned_user_id=owner_for_occurrence))

    now = datetime.now(UTC)
    rows = bind.execute(
        sa.select(meeting_occurrence.c.id, meeting_series.c.owner_user_id).select_from(
            meeting_occurrence.join(
                meeting_series, meeting_occurrence.c.series_id == meeting_series.c.id
            )
        )
    )
    bind.execute(
        sa.insert(attendee),
        [
            {
                "id": uuid.uuid4(),
                "occurrence_id": occurrence_id,
                "user_id": owner_user_id,
                "created_at": now,
            }
            for occurrence_id, owner_user_id in rows
        ],
    )

    with op.batch_alter_table("task") as batch:
        batch.alter_column(
            "assigned_user_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("task") as batch:
        batch.drop_constraint("fk_task_assigned_user", type_="foreignkey")
        batch.drop_index("ix_task_assigned_user_id")
        batch.drop_column("assigned_user_id")

    op.drop_index(
        "ix_meeting_occurrence_attendee_user_id", table_name="meeting_occurrence_attendee"
    )
    op.drop_index(
        "ix_meeting_occurrence_attendee_occurrence_id",
        table_name="meeting_occurrence_attendee",
    )
    op.drop_table("meeting_occurrence_attendee")
