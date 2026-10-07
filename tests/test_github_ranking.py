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
    content.raw_metadata['github_star_observation'] = {'delta': delta}
    return content


def test_mixed_selection_preserves_growth_then_cumulative_and_deduplicates(sample):
    items = [candidate(sample, 'a', 100, 50), candidate(sample, 'b', 200, 50), candidate(sample, 'c', 50, 90), candidate(sample, 'd', 10000, None), candidate(sample, 'e', 9000, -2), candidate(sample, 'f', 8000, 1)]
    assert [c.external_id for c in ranking.select_github(items + [items[0]])] == ['c', 'b', 'a', 'd', 'e']
    assert items[0].raw_metadata == {'github_star_observation': {'delta': 50}}


@pytest.mark.parametrize('count', range(5))
def test_unmeasured_fill_and_small_candidates(sample, count):
    items = [candidate(sample, str(i), i, None) for i in range(count)]
    assert [c.external_id for c in ranking.select_github(items)] == [str(i) for i in reversed(range(count))]


def test_attach_evidence_does_not_mutate_input(sample):
    content = ranking.with_observation(sample, NOW, [ranking.StarObservation(NOW-timedelta(hours=24), sample.metrics.stars-10)])
    assert 'github_star_observation' not in sample.raw_metadata
    assert content.raw_metadata['github_star_observation']['delta'] == 10
    assert content.raw_metadata['github_star_observation']['elapsed_seconds'] == 86400
