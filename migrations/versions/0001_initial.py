"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-02-19

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "meeting_series",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("default_interval_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_series_owner"),
    )
    op.create_index(
        "ix_meeting_series_owner_user_id", "meeting_series", ["owner_user_id"], unique=False
    )

    op.create_table(
        "meeting_occurrence",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("series_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["series_id"], ["meeting_series.id"], name="fk_occurrence_series"),
    )
    op.create_index(
        "ix_meeting_occurrence_series_id", "meeting_occurrence", ["series_id"], unique=False
    )
    op.create_index(
        "ix_meeting_occurrence_scheduled_at",
        "meeting_occurrence",
        ["scheduled_at"],
        unique=False,
    )

    op.create_table(
        "task",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("series_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("is_done", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["series_id"], ["meeting_series.id"], name="fk_task_series"),
    )
    op.create_index("ix_task_series_id", "task", ["series_id"], unique=False)

    op.create_table(
        "agenda_item",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("occurrence_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_done", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["occurrence_id"], ["meeting_occurrence.id"], name="fk_agenda_occurrence"
        ),
    )
    op.create_index("ix_agenda_item_occurrence_id", "agenda_item", ["occurrence_id"], unique=False)

    op.create_table(
        "reminder",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("occurrence_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("email", "slack", name="reminder_channel"),
            nullable=False,
        ),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["occurrence_id"], ["meeting_occurrence.id"], name="fk_reminder_occurrence"
        ),
    )
    op.create_index("ix_reminder_occurrence_id", "reminder", ["occurrence_id"], unique=False)
    op.create_index("ix_reminder_send_at", "reminder", ["send_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reminder_send_at", table_name="reminder")
    op.drop_index("ix_reminder_occurrence_id", table_name="reminder")
    op.drop_table("reminder")

    op.drop_index("ix_agenda_item_occurrence_id", table_name="agenda_item")
    op.drop_table("agenda_item")

    op.drop_index("ix_task_series_id", table_name="task")
    op.drop_table("task")

    op.drop_index("ix_meeting_occurrence_scheduled_at", table_name="meeting_occurrence")
    op.drop_index("ix_meeting_occurrence_series_id", table_name="meeting_occurrence")
    op.drop_table("meeting_occurrence")

    op.drop_index("ix_meeting_series_owner_user_id", table_name="meeting_series")
    op.drop_table("meeting_series")

    op.drop_table("users")

    # Enum cleanup (Postgres only)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS reminder_channel")
