"""capture per-run GitHub stars observations"""

import re

import sqlalchemy as sa
from alembic import op

revision = "0004_github_star_snapshots"
down_revision = "0003_report_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "github_star_snapshots" in inspector.get_table_names():
        columns = {column["name"]: column for column in inspector.get_columns("github_star_snapshots")}
        unique = any(set(c["column_names"]) == {"external_id", "observation_id"}
                     for c in inspector.get_unique_constraints("github_star_snapshots"))
        index = any(i["column_names"] == ["external_id", "observed_at"]
                    for i in inspector.get_indexes("github_star_snapshots"))
        foreign = any(f["constrained_columns"] == ["observation_id"]
                      and f["referred_table"] == "runs" and f["referred_columns"] == ["id"]
                      for f in inspector.get_foreign_keys("github_star_snapshots"))
        required = {"id", "external_id", "observation_id", "observed_at", "stars"}
        complete = required <= columns.keys() and all(
            not columns[name]["nullable"] for name in required - {"id"}
        )
        primary_key = inspector.get_pk_constraint("github_star_snapshots")["constrained_columns"] == ["id"]
        expected_types = {
            "id": sa.Integer, "external_id": sa.String, "observation_id": sa.Integer,
            "observed_at": sa.DateTime, "stars": sa.Integer,
        }
        compatible_types = required <= columns.keys() and all(
            isinstance(columns[name]["type"], expected)
            for name, expected in expected_types.items()
        )
        # SQLite only generates omitted primary keys for the exact INTEGER type.
        if compatible_types and op.get_bind().dialect.name == "sqlite":
            compatible_types = str(columns["id"]["type"]) == "INTEGER"
        generated_id = True
        if op.get_bind().dialect.name == "postgresql":
            id_column = columns.get("id", {})
            default = id_column.get("default")
            generated_id = bool(id_column.get("identity")) or (
                isinstance(default, str)
                and re.match(r"^\s*(?:pg_catalog\.)?nextval\s*\(", default, re.IGNORECASE) is not None
            )
        if not (complete and primary_key and compatible_types and generated_id
                and unique and index and foreign):
            raise RuntimeError("github_star_snapshots table has a partially applied 0004 migration")
        return
    op.create_table(
        "github_star_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("observation_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.UniqueConstraint("external_id", "observation_id", name="uq_github_star_observation"),
    )
    op.create_index("ix_github_star_external_observed", "github_star_snapshots", ["external_id", "observed_at"])


def downgrade() -> None:
    op.drop_table("github_star_snapshots")
