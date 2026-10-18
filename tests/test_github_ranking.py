from datetime import datetime, timedelta, timezone

import pytest

from glean_ai import github_ranking as ranking

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize('hours,delta', [(22, 20), (26, 20), (21.99, None), (26.01, None), (48, None), (-1, None), (1, None)])
def test_baseline_window(hours, delta):
    change = ranking.star_change(NOW, 100, [ranking.StarObservation(NOW - timedelta(hours=hours), 80)])
    assert change.delta == delta


def test_closest_baseline_tie_uses_earlier_and_keeps_negative():
    observations = [ranking.StarObservation(NOW-timedelta(hours=23), 90), ranking.StarObservation(NOW-timedelta(hours=25), 105)]
    change = ranking.star_change(NOW, 100, observations)
    assert change.delta == -5
    assert change.elapsed_seconds == 90000
    assert change.baseline_at == NOW - timedelta(hours=25)


def test_zero_and_missing_are_different():
    assert ranking.star_change(NOW, 100, []).delta is None
    assert ranking.star_change(NOW, 100, [ranking.StarObservation(NOW-timedelta(hours=24), 100)]).delta == 0


def candidate(sample, identifier, stars, delta):
    content = sample.model_copy(deep=True)
    content.external_id = identifier
    content.metrics.stars = stars
    content.raw_metadata['github_star_observation'] = {'delta': delta, 'elapsed_seconds': 86400,
        'observed_at': NOW.isoformat(), 'baseline_at': (NOW-timedelta(hours=24)).isoformat()}
    return content


def test_mixed_selection_preserves_cumulative_then_growth_and_deduplicates(sample):
    items = [candidate(sample, 'a', 100, 50), candidate(sample, 'b', 200, 50), candidate(sample, 'c', 50, 90), candidate(sample, 'd', 10000, None), candidate(sample, 'e', 9000, -2), candidate(sample, 'f', 8000, 1)]
    assert [c.external_id for c in ranking.select_github(items + [items[0]])] == ['d', 'e', 'f', 'c', 'b']
    assert items[0].raw_metadata['github_star_observation']['delta'] == 50
    assert items[0].raw_metadata['github_star_observation']['elapsed_seconds'] == 86400


@pytest.mark.parametrize('count', range(5))
def test_unmeasured_candidates_only_take_cumulative_seats(sample, count):
    items = [candidate(sample, str(i), i, None) for i in range(count)]
    assert [c.external_id for c in ranking.select_github(items)] == [str(i) for i in reversed(range(count))][:3]


def test_attach_evidence_does_not_mutate_input(sample):
    content = ranking.with_observation(sample, NOW, [ranking.StarObservation(NOW-timedelta(hours=24), sample.metrics.stars-10)])
    assert 'github_star_observation' not in sample.raw_metadata
    assert content.raw_metadata['github_star_observation']['delta'] == 10
    assert content.raw_metadata['github_star_observation']['elapsed_seconds'] == 86400


def test_growth_normalizes_duration_and_skips_cumulative_overlap(sample):
    items = [candidate(sample, name, stars, delta) for name, stars, delta in
             [('a', 1000, 999), ('b', 900, None), ('c', 800, 0),
              ('d', 20, 24), ('e', 10, 23), ('f', 30, 1)]]
    items[3].raw_metadata['github_star_observation']['elapsed_seconds'] = 26 * 3600
    items[4].raw_metadata['github_star_observation']['elapsed_seconds'] = 22 * 3600
    items[3].raw_metadata['github_star_observation']['baseline_at'] = (NOW-timedelta(hours=26)).isoformat()
    items[4].raw_metadata['github_star_observation']['baseline_at'] = (NOW-timedelta(hours=22)).isoformat()
    assert [c.external_id for c in ranking.select_github(items)] == ['a', 'b', 'c', 'e', 'd']


@pytest.mark.parametrize('delta,elapsed', [(None, 86400), (0, 86400), (-1, 86400),
    (True, 86400), (float('inf'), 86400), (1, None), (1, True),
    (1, 0), (1, -1), (1, 79199), (1, 93601), (1, float('nan')), (1, '86400')])
def test_invalid_growth_never_fills_empty_seats(sample, delta, elapsed):
    items = [candidate(sample, name, 100, None) for name in ['c', 'b', 'a']]
    extra = candidate(sample, 'd', 10, delta)
    extra.raw_metadata['github_star_observation']['elapsed_seconds'] = elapsed
    assert [c.external_id for c in ranking.select_github(items + [extra])] == ['a', 'b', 'c']


@pytest.mark.parametrize('field,value', [
    ('baseline_at', None), ('baseline_at', 'bad'),
    ('baseline_at', '2026-10-07T00:00:00'),
    ('baseline_at', '2026-10-07T01:00:00+00:00'),
    ('observed_at', None), ('observed_at', 'bad'),
    ('observed_at', '2026-10-08T00:00:00'),
    ('observed_at', '2026-10-08T01:00:00+00:00'),
])
def test_growth_requires_consistent_aware_observation_timestamps(sample, field, value):
    items = [candidate(sample, name, 100, None) for name in ['a', 'b', 'c']]
    extra = candidate(sample, 'd', 10, 1)
    extra.raw_metadata['github_star_observation'][field] = value
    assert [item.external_id for item in ranking.select_github(items + [extra])] == ['a', 'b', 'c']
