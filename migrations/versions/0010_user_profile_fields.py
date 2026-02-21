"""Add user profile fields.

Revision ID: 0010
Revises: 0009
Create Date: 2026-02-21
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _fallback_name(email: str, fallback: str) -> str:
    local = email.split("@", 1)[0].strip()
    return local or fallback


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("first_name", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("last_name", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("timezone", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("email", sa.String(length=320)),
        sa.column("first_name", sa.String(length=120)),
        sa.column("last_name", sa.String(length=120)),
        sa.column("timezone", sa.String(length=64)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    existing = bind.execute(sa.select(users.c.id, users.c.email, users.c.created_at)).all()
    for user_id, email, created_at in existing:
        first = _fallback_name(email, "User")
        now = created_at if created_at is not None else datetime.now(UTC)
        bind.execute(
            sa.update(users)
            .where(users.c.id == user_id)
            .values(
                first_name=first,
                last_name="",
                timezone="UTC",
                updated_at=now,
            )
        )

    with op.batch_alter_table("users") as batch:
        batch.alter_column("first_name", existing_type=sa.String(length=120), nullable=False)
        batch.alter_column("last_name", existing_type=sa.String(length=120), nullable=False)
        batch.alter_column("timezone", existing_type=sa.String(length=64), nullable=False)
        batch.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("timezone")
        batch.drop_column("last_name")
        batch.drop_column("first_name")
