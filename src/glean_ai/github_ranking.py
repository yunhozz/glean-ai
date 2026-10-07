"""GitHub ranking from measured net stars and cumulative stars."""

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
    def delta(content: Content) -> int | float:
        value = content.raw_metadata.get('github_star_observation', {}).get('delta')
        return value if isinstance(value, (int, float)) else 0
    growth = sorted((c for c in cumulative if delta(c) > 0), key=lambda c: (-delta(c), -c.metrics.stars, c.external_id))
    selected = growth[:3]
    identifiers = {c.external_id for c in selected}
    remaining = [c for c in cumulative if c.external_id not in identifiers]
    selected.extend(remaining[:2])
    # Fill any unoccupied growth seats using the next cumulative leaders.
    selected.extend(remaining[2:2 + max(0, 5 - len(selected))])
    return selected[:max(0, min(limit, 5))]
