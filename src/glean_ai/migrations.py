from sqlalchemy import Column, Connection, MetaData, String, Table, inspect

LEGACY_TABLES = {"contents", "runs", "reports"}
RUN_DETAIL_COLUMNS = {"error_code", "fetched_count", "accepted_count"}


def adopt_legacy_schema(connection: Connection) -> None:
    """Record schemas created by the former ``init-db`` deployment command."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables or not LEGACY_TABLES.issubset(tables):
        return

    run_columns = {column["name"] for column in inspector.get_columns("runs")}
    present_details = run_columns & RUN_DETAIL_COLUMNS
    if present_details == set():
        revision = "0001"
    elif present_details == RUN_DETAIL_COLUMNS:
        revision = "0002_collection_run_details"
    else:
        raise RuntimeError("runs table has a partially applied 0002 migration")

    version_table = Table(
        "alembic_version",
        MetaData(),
        Column("version_num", String(32), nullable=False),
    )
    version_table.create(connection)
    connection.execute(version_table.insert().values(version_num=revision))
