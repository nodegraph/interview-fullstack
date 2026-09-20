"""Add renewable import ownership and visible partial failures.

Revision ID: 005
Revises: 004
"""
import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("catalog_import_jobs", sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("catalog_import_jobs", sa.Column("warnings", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("catalog_import_jobs", sa.Column("owner_token", sa.String(36)))
    op.add_column("catalog_import_jobs", sa.Column("lease_expires_at", sa.Float()))


def downgrade() -> None:
    for column in ("lease_expires_at", "owner_token", "warnings", "warning_count"):
        op.drop_column("catalog_import_jobs", column)
