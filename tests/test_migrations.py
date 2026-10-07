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


def test_adopt_legacy_schema_with_report_deliveries() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        create_legacy_tables(connection, {"error_code", "fetched_count", "accepted_count"})
        connection.execute(sa.text(
            "CREATE TABLE report_deliveries ("
            "id INTEGER PRIMARY KEY, report_date VARCHAR(10) NOT NULL, "
            "source VARCHAR(32) NOT NULL, sent_at DATETIME NOT NULL, "
            "UNIQUE(report_date, source))"
        ))
        adopt_legacy_schema(connection)
        revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
    assert revision == "0003_report_deliveries"


def test_adopt_old_runs_before_report_delivery_migration() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        create_legacy_tables(connection, set())
        connection.execute(sa.text(
            "CREATE TABLE report_deliveries ("
            "id INTEGER PRIMARY KEY, report_date VARCHAR(10) NOT NULL, "
            "source VARCHAR(32) NOT NULL, sent_at DATETIME NOT NULL, "
            "UNIQUE(report_date, source))"
        ))
        adopt_legacy_schema(connection)
        revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
    assert revision == "0001"


def test_adopt_legacy_schema_rejects_incomplete_report_deliveries() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        create_legacy_tables(connection, {"error_code", "fetched_count", "accepted_count"})
        connection.execute(sa.text("CREATE TABLE report_deliveries (id INTEGER PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="partially applied"):
            adopt_legacy_schema(connection)


def test_github_snapshot_migration_preserves_existing_data_and_downgrades():
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path

    spec = importlib.util.spec_from_file_location('snapshot_migration', Path('migrations/versions/0004_github_star_snapshots.py'))
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        create_legacy_tables(connection, set())
        connection.execute(sa.text('INSERT INTO contents (id) VALUES (7)'))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()
        assert connection.scalar(sa.text('SELECT id FROM contents')) == 7
        assert connection.scalar(sa.text('SELECT count(*) FROM github_star_snapshots')) == 0
        migration.downgrade()
        assert 'github_star_snapshots' not in sa.inspect(connection).get_table_names()
        assert connection.scalar(sa.text('SELECT id FROM contents')) == 7


def test_github_snapshot_migration_rejects_partial_schema():
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('snapshot_migration', 'migrations/versions/0004_github_star_snapshots.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE github_star_snapshots (id INTEGER PRIMARY KEY)'))
        migration.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match='partially applied'):
            migration.upgrade()


def test_alembic_upgrade_adopts_create_all_schema(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from glean_ai.storage import Store
    from glean_ai.config import Settings
    import glean_ai.config as configuration
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    store = Store(database_url)
    store.create_all()
    monkeypatch.setattr(configuration, 'get_settings', lambda: Settings(database_url=database_url))
    command.upgrade(Config('alembic.ini'), 'head')
    with store.engine.connect() as connection:
        assert connection.scalar(sa.text('SELECT version_num FROM alembic_version')) == '0004_github_star_snapshots'
    command.downgrade(Config('alembic.ini'), '0003_report_deliveries')
    assert 'github_star_snapshots' not in sa.inspect(store.engine).get_table_names()
    assert {'contents', 'runs', 'reports', 'report_deliveries'} <= set(sa.inspect(store.engine).get_table_names())


@pytest.mark.parametrize('malformation', ['missing_pk', 'id_type', 'id_bigint', 'external_id_type', 'observation_id_type', 'observed_at_type', 'stars_type'])
def test_github_snapshot_adoption_rejects_malformed_key_or_types(malformation):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location(
        'snapshot_migration', 'migrations/versions/0004_github_star_snapshots.py'
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = sa.MetaData()
    sa.Table('runs', metadata, sa.Column('id', sa.Integer(), primary_key=True))
    types = {
        'id': sa.Integer(), 'external_id': sa.String(255),
        'observation_id': sa.Integer(), 'observed_at': sa.DateTime(), 'stars': sa.Integer(),
    }
    if malformation == 'id_bigint':
        types['id'] = sa.BigInteger()
    if malformation.endswith('_type'):
        column = malformation.removesuffix('_type')
        types[column] = sa.Integer() if column in {'external_id', 'observed_at'} else sa.String(255)
    snapshots = sa.Table(
        'github_star_snapshots', metadata,
        sa.Column('id', types['id'], primary_key=malformation != 'missing_pk', nullable=False),
        sa.Column('external_id', types['external_id'], nullable=False),
        sa.Column('observation_id', types['observation_id'], sa.ForeignKey('runs.id'), nullable=False),
        sa.Column('observed_at', types['observed_at'], nullable=False),
        sa.Column('stars', types['stars'], nullable=False),
        sa.UniqueConstraint('external_id', 'observation_id'),
    )
    sa.Index('ix_observation_lookup', snapshots.c.external_id, snapshots.c.observed_at)
    engine = sa.create_engine('sqlite://')
    metadata.create_all(engine)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match='partially applied'):
            migration.upgrade()
        assert connection.scalar(sa.text('SELECT count(*) FROM github_star_snapshots')) == 0


@pytest.mark.parametrize(
    ('default', 'identity', 'accepted'),
    [
        (None, None, False),
        ('1', None, False),
        ("nextval('github_star_snapshots_id_seq'::regclass)", None, True),
        (None, {'always': False, 'start': 1, 'increment': 1}, True),
        (None, {'always': True, 'start': 1, 'increment': 1}, True),
    ],
)
def test_postgresql_snapshot_adoption_requires_generated_id(monkeypatch, default, identity, accepted):
    import importlib.util
    from types import SimpleNamespace
    from sqlalchemy.dialects import postgresql
    from glean_ai.storage import Store

    spec = importlib.util.spec_from_file_location(
        'snapshot_migration', 'migrations/versions/0004_github_star_snapshots.py'
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    store = Store('sqlite://')
    store.create_all()
    inspector = sa.inspect(store.engine)
    columns = [dict(column) for column in inspector.get_columns('github_star_snapshots')]
    for column in columns:
        if column['name'] == 'id':
            column['default'] = default
            column['identity'] = identity
    class PostgreSQLInspector:
        def get_columns(self, table):
            return columns
        def __getattr__(self, name):
            return getattr(inspector, name)
    monkeypatch.setattr(migration.sa, 'inspect', lambda connection: PostgreSQLInspector())
    migration.op = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()))
    if accepted:
        migration.upgrade()
    else:
        with pytest.raises(RuntimeError, match='partially applied'):
            migration.upgrade()
