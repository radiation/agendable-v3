"""Add global user role.

Revision ID: 0011
Revises: 0010
Create Date: 2026-02-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role_enum = sa.Enum("user", "admin", name="userrole")
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        role_enum.create(bind, checkfirst=True)
        op.add_column(
            "users",
            sa.Column(
                "role",
                role_enum,
                nullable=False,
                server_default="user",
            ),
        )
        return

    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "role",
                role_enum,
                nullable=False,
                server_default="user",
            )
        )


def downgrade() -> None:
    role_enum = sa.Enum("user", "admin", name="userrole")
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.drop_column("users", "role")
        role_enum.drop(bind, checkfirst=True)
        return

    with op.batch_alter_table("users") as batch:
        batch.drop_column("role")
