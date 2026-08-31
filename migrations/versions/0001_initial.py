"""initial schema"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

def upgrade() -> None:
    op.create_table("contents", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source", sa.String(32), nullable=False), sa.Column("external_id", sa.String(255), nullable=False), sa.Column("content_type", sa.String(32), nullable=False), sa.Column("author", sa.String(255)), sa.Column("title", sa.Text(), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("url", sa.Text(), nullable=False), sa.Column("published_at", sa.DateTime(timezone=True), nullable=False), sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False), sa.Column("language", sa.String(16)), sa.Column("metrics", sa.JSON(), nullable=False), sa.Column("raw_metadata", sa.JSON(), nullable=False), sa.Column("matched_keywords", sa.JSON(), nullable=False), sa.Column("categories", sa.JSON(), nullable=False), sa.Column("topic_id", sa.String(64)), sa.Column("relevance_score", sa.Float(), nullable=False), sa.Column("trend_score", sa.Float(), nullable=False), sa.Column("quality_score", sa.Float(), nullable=False), sa.Column("freshness_score", sa.Float(), nullable=False), sa.Column("final_score", sa.Float(), nullable=False), sa.Column("score_reasons", sa.JSON(), nullable=False), sa.UniqueConstraint("source", "external_id", name="uq_source_external_id"))
    op.create_table("runs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("kind", sa.String(32), nullable=False), sa.Column("source", sa.String(32)), sa.Column("status", sa.String(32), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("error", sa.Text()))
    op.create_table("reports", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("report_date", sa.String(10), unique=True, nullable=False), sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False))

def downgrade() -> None:
    op.drop_table("reports")
    op.drop_table("runs")
    op.drop_table("contents")
