from copy import deepcopy
from datetime import datetime, timedelta, timezone

from glean_ai.pipeline import canonical_url, deduplicate, process, score


def test_url_and_text_dedup(sample):
    duplicate = deepcopy(sample)
    duplicate.external_id = "2"
    duplicate.url = "https://github.com/acme/agent"
    assert canonical_url(str(sample.url)) == "https://github.com/acme/agent"
    assert len(deduplicate([sample, duplicate])) == 1


def test_classification_and_explainable_score(sample):
    result = process([sample], ["AI agent", "MCP"])[0]
    assert "개발/에이전트" in result.categories
    assert 0 <= result.final_score <= 100
    assert set(result.score_reasons) == {"relevance", "trend", "quality", "freshness"}


def test_freshness_changes_score(sample):
    old = deepcopy(sample)
    old.published_at = datetime.now(timezone.utc) - timedelta(days=8)
    assert score([sample])[0].freshness_score > score([old])[0].freshness_score
