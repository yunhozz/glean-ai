"""track Slack delivery per report source

Revision ID: 0003_report_deliveries
Revises: 0002_collection_run_details
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_report_deliveries"
down_revision = "0002_collection_run_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "report_deliveries" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("report_deliveries")}
        unique = any(
            set(constraint["column_names"]) == {"report_date", "source"}
            for constraint in inspector.get_unique_constraints("report_deliveries")
        )
        if not {"id", "report_date", "source", "sent_at"}.issubset(columns) or not unique:
            raise RuntimeError("report_deliveries table has a partially applied 0003 migration")
        return
    op.create_table(
        "report_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_date", sa.String(10), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("report_date", "source", name="uq_report_delivery_source"),
    )


def downgrade() -> None:
    op.drop_table("report_deliveries")
