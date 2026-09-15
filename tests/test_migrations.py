import pytest
import sqlalchemy as sa

from glean_ai.migrations import adopt_legacy_schema


def create_legacy_tables(connection: sa.Connection, detail_columns: set[str]) -> None:
    connection.execute(sa.text("CREATE TABLE contents (id INTEGER PRIMARY KEY)"))
    columns = ", ".join(f"{name} INTEGER" for name in sorted(detail_columns))
    suffix = f", {columns}" if columns else ""
    connection.execute(sa.text(f"CREATE TABLE runs (id INTEGER PRIMARY KEY{suffix})"))
    connection.execute(sa.text("CREATE TABLE reports (id INTEGER PRIMARY KEY)"))


@pytest.mark.parametrize(
    ("detail_columns", "expected_revision"),
    [
        (set(), "0001"),
        ({"error_code", "fetched_count", "accepted_count"}, "0002_collection_run_details"),
    ],
)
def test_adopt_legacy_schema(detail_columns: set[str], expected_revision: str) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        create_legacy_tables(connection, detail_columns)
        adopt_legacy_schema(connection)
        revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
    assert revision == expected_revision


def test_adopt_legacy_schema_rejects_partial_migration() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        create_legacy_tables(connection, {"error_code"})
        with pytest.raises(RuntimeError, match="partially applied"):
            adopt_legacy_schema(connection)
