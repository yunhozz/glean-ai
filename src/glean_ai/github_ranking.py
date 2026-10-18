"""GitHub ranking from measured net stars and cumulative stars."""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import Content


@dataclass(frozen=True)
class StarObservation:
    observed_at: datetime
    stars: int


@dataclass(frozen=True)
class StarChange:
    baseline_at: datetime | None = None
    delta: int | None = None
    elapsed_seconds: float | None = None


def star_change(now: datetime, stars: int, history: Iterable[StarObservation]) -> StarChange:
    eligible = [row for row in history if timedelta(hours=22) <= now-row.observed_at <= timedelta(hours=26)]
    if not eligible:
        return StarChange()
    baseline = min(eligible, key=lambda row: (abs((now-row.observed_at).total_seconds()-86400), row.observed_at))
    return StarChange(baseline.observed_at, stars-baseline.stars, (now-baseline.observed_at).total_seconds())


def with_observation(content: Content, now: datetime, history: Iterable[StarObservation]) -> Content:
    result = content.model_copy(deep=True)
    change = star_change(now, content.metrics.stars, history)
    result.raw_metadata['github_star_observation'] = {
        'observed_at': now.isoformat(),
        'baseline_at': change.baseline_at.isoformat() if change.baseline_at else None,
        'delta': change.delta,
        'elapsed_seconds': change.elapsed_seconds,
    }
    return result


def select_github(contents: Iterable[Content], limit: int = 5) -> list[Content]:
    unique = {content.external_id: content for content in contents if content.source == 'github'}
    cumulative = sorted(unique.values(), key=lambda c: (-c.metrics.stars, c.external_id))
    def growth_rate(content: Content) -> float:
        observation = content.raw_metadata.get('github_star_observation', {})
        if not isinstance(observation, dict):
            return 0
        delta = observation.get('delta')
        elapsed = observation.get('elapsed_seconds')
        if any(not isinstance(value, (int, float)) or isinstance(value, bool)
               or not math.isfinite(value) for value in (delta, elapsed)):
            return 0
        if delta <= 0 or not 22 * 3600 <= elapsed <= 26 * 3600:
            return 0
        try:
            baseline = datetime.fromisoformat(observation.get('baseline_at'))
            observed = datetime.fromisoformat(observation.get('observed_at'))
            if baseline.utcoffset() is None or observed.utcoffset() is None:
                return 0
            measured_elapsed = (observed - baseline).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
        if measured_elapsed != elapsed:
            return 0
        return delta * (86400 / elapsed)

    selected = cumulative[:3]
    identifiers = {content.external_id for content in selected}
    growth = sorted(
        (content for content in cumulative
         if content.external_id not in identifiers and growth_rate(content) > 0),
        key=lambda content: (-growth_rate(content), -content.metrics.stars, content.external_id),
    )
    selected.extend(growth[:2])
    return selected[:max(0, min(limit, 5))]
