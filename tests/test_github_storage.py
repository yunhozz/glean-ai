from datetime import datetime, timezone

import sqlalchemy as sa
import pytest

from glean_ai.storage import Store


def test_snapshot_schema_and_latest_run():
    store = Store('sqlite://')
    store.create_all()
    inspector = sa.inspect(store.engine)
    assert 'github_star_snapshots' in inspector.get_table_names()
    columns = {c['name'] for c in inspector.get_columns('github_star_snapshots')}
    assert {'id', 'external_id', 'observation_id', 'observed_at', 'stars'} <= columns
    assert any(set(c['column_names']) == {'external_id', 'observation_id'} for c in inspector.get_unique_constraints('github_star_snapshots'))
    assert any(set(i['column_names']) == {'external_id', 'observed_at'} for i in inspector.get_indexes('github_star_snapshots'))
    assert inspector.get_foreign_keys('github_star_snapshots')[0]['referred_table'] == 'runs'
    assert store.latest_github_run() is None


def result_for(items, status='success'):
    from glean_ai.models import CollectionResult
    return CollectionResult(source='github', status=status, contents=items, accepted_count=len(items))


def test_collection_refresh_dedup_retention_and_latest_status(sample):
    from datetime import timedelta
    from glean_ai.storage import ContentRow, RunRow
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    store = Store('sqlite://')
    store.create_all()
    store.persist_github_collection([sample], result_for([sample]), now-timedelta(days=8))
    store.persist_github_collection([sample], result_for([sample]), now-timedelta(days=7))
    changed = sample.model_copy(deep=True)
    changed.metrics.stars = 500
    changed.title = 'Updated repository'
    store.persist_github_collection([changed, changed], result_for([changed]), now)
    with store.session() as session:
        rows = session.query(ContentRow).all()
        assert len(rows) == 1
        assert rows[0].metrics['stars'] == 500
        assert rows[0].title == 'Updated repository'
        snapshots = session.execute(sa.text('SELECT stars, observed_at FROM github_star_snapshots ORDER BY observed_at')).all()
        assert [r.stars for r in snapshots] == [100, 500]
        assert session.query(RunRow).count() == 3
    assert store.latest_github_run().started_at == now
    store.persist_github_collection([], result_for([], 'empty'), now+timedelta(minutes=1))
    assert store.latest_github_run().status == 'empty'


def test_snapshot_failure_rolls_back_content_and_run(sample):
    from sqlalchemy import event
    from glean_ai.storage import ContentRow, RunRow
    store = Store('sqlite://')
    store.create_all()
    def reject_snapshot(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO github_star_snapshots'):
            raise RuntimeError('snapshot failed')
    event.listen(store.engine, 'before_cursor_execute', reject_snapshot)
    import pytest
    with pytest.raises(RuntimeError, match='snapshot failed'):
        store.persist_github_collection([sample], result_for([sample]), datetime.now(timezone.utc))
    with store.session() as session:
        assert session.query(ContentRow).count() == 0
        assert session.query(RunRow).count() == 0


@pytest.mark.parametrize('status', ['success', 'partial', 'empty', 'failed', 'disabled', 'not_configured'])
def test_latest_run_masks_older_candidates_and_uses_observation_time(sample, status):
    from datetime import timedelta
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    store = Store('sqlite://')
    store.create_all()
    sample.published_at = now-timedelta(days=30)
    store.persist_github_collection([sample], result_for([sample]), now-timedelta(hours=24))
    latest = [sample] if status in {'success', 'partial'} else []
    store.persist_github_collection(latest, result_for(latest, status), now)
    rows = store.github_report_rows(now)
    assert len(rows) == (1 if latest else 0)
    if rows:
        assert rows[0][1].observed_at.replace(tzinfo=timezone.utc) == now
    assert store.github_report_rows(now+timedelta(hours=24, microseconds=1)) == []
    assert store.github_report_rows(now-timedelta(seconds=1)) == []
