"""Add external identities

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-19

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_identities",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_external_identity_user"),
        sa.UniqueConstraint("provider", "subject", name="uq_external_identity_provider_subject"),
    )
    op.create_index(
        "ix_external_identities_user_id", "external_identities", ["user_id"], unique=False
    )
    op.create_index(
        "ix_external_identities_provider", "external_identities", ["provider"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_external_identities_provider", table_name="external_identities")
    op.drop_index("ix_external_identities_user_id", table_name="external_identities")
    op.drop_table("external_identities")
