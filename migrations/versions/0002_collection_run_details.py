"""add collection run details

Revision ID: 0002_collection_run_details
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_collection_run_details"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column("runs", sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("runs", "accepted_count")
    op.drop_column("runs", "fetched_count")
    op.drop_column("runs", "error_code")
