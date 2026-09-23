from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from glean_ai.reporters import SlackReporter, build_blocks
from glean_ai.models import CollectionResult, CollectionStatus, Metrics, TopicSummary
from glean_ai.storage import Store
from glean_ai.summarizer import Summarizer


def test_storage_idempotency(tmp_path, sample):
    store = Store(f"sqlite:///{tmp_path}/test.db")
    store.create_all()
    assert store.upsert(sample) is True
    assert store.upsert(sample) is False
    rows = store.recent(datetime.now(timezone.utc) - timedelta(days=1))
    assert rows[0].title == sample.title


def test_huggingface_fallback_describes_trend_without_claiming_update(sample):
    model = deepcopy(sample)
    model.source = "huggingface"
    summary = Summarizer.fallback(model)

    assert "주목받는 모델" in summary.title_ko
    assert "현재 트렌드" in summary.summary_ko
    assert "최신 변경" not in summary.summary_ko


def test_huggingface_recollection_updates_metrics_and_report_recency(tmp_path, sample):
    store = Store(f"sqlite:///{tmp_path}/trends.db")
    store.create_all()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=1)
    model = deepcopy(sample)
    model.source = "huggingface"
    model.published_at = now - timedelta(days=8)
    model.collected_at = now - timedelta(days=2)
    model.raw_metadata = {"trending_score": 1}
    model.metrics = Metrics(likes=50)
    assert store.upsert(model) is True
    assert store.recent(since) == []

    updated = deepcopy(model)
    updated.collected_at = now
    updated.metrics.likes = 100
    updated.raw_metadata = {"trending_score": 10}
    updated.final_score = 80
    assert store.upsert(updated) is False

    rows = store.recent(since)
    assert len(rows) == 1
    assert rows[0].metrics["likes"] == 100
    assert rows[0].raw_metadata["trending_score"] == 10
    assert rows[0].final_score == 80

    old_github = deepcopy(sample)
    old_github.published_at = now - timedelta(days=8)
    old_github.collected_at = now
    old_github.external_id = "old-github"
    assert store.upsert(old_github) is True
    assert [row.external_id for row in store.recent(since)] == [model.external_id]


@pytest.mark.asyncio
async def test_llm_fallback_and_blocks(sample):
    async with httpx.AsyncClient() as client:
        summary = await Summarizer(client, None, "https://example.com", "model").summarize(sample)
    blocks = build_blocks([(sample, summary)], datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc))
    assert blocks[0]["text"]["text"] == "🤖 오늘의 AI 브리프"
    assert any(sample.title in str(block) for block in blocks)
    assert any("실무 포인트" in str(block) for block in blocks)
    assert all("왜 중요한가" not in str(block) for block in blocks)
    assert "Open source workflow automation" not in str(blocks)


def test_blocks_show_three_detailed_items_and_compact_remainder(sample):
    rows = []
    for index in range(5):
        content = deepcopy(sample)
        content.external_id = str(index)
        content.title = f"Original title {index}"
        summary = TopicSummary(
            title_ko=f"소식 {index}",
            summary_ko=f"상세 요약 {index}",
            areas=["개발"],
            why_important=f"실무 내용 {index}",
        )
        rows.append((content, summary))

    now = datetime.now(timezone.utc)
    blocks = build_blocks(rows, now - timedelta(days=1), now)
    text = str(blocks)

    assert "🔥 오늘의 주목할 소식" in text
    assert "📌 함께 볼 소식" in text
    assert all(f"상세 요약 {index}" in text for index in range(3))
    assert all(f"상세 요약 {index}" not in text for index in range(3, 5))
    assert all(f"소식 {index}" in text for index in range(5))
    assert sum(block["type"] == "divider" for block in blocks) == 3
    assert len(blocks) <= 50
    assert all(
        len(block["text"]["text"]) <= 2900
        for block in blocks
        if block["type"] == "section"
    )


def test_blocks_show_github_metrics_and_reddit_daily_rank(sample):
    github = deepcopy(sample)
    github.metrics = Metrics(stars=0, forks=0)
    reddit = deepcopy(sample)
    reddit.source = "reddit"
    reddit.external_id = "reddit-1"
    reddit.metrics = Metrics()
    reddit.raw_metadata = {"daily_rank": 2}
    summary = TopicSummary(
        title_ko="AI 소식",
        summary_ko="요약",
        areas=["개발"],
        why_important="실무 내용",
    )

    now = datetime.now(timezone.utc)
    blocks = build_blocks(
        [(github, summary), (reddit, summary)], now - timedelta(days=1), now
    )
    text = str(blocks)

    assert "GitHub · ⭐ 0 · Fork 0" in text
    assert "Reddit · 🔥 최근 24시간 인기 #2" in text
    assert "공개 지표 없음" not in text


def test_blocks_show_partial_collection_status():
    now = datetime.now(timezone.utc)
    blocks = build_blocks([], now - timedelta(days=1), now, [
        CollectionResult(
            source="github", status=CollectionStatus.SUCCESS, accepted_count=3
        ),
        CollectionResult(
            source="reddit",
            status=CollectionStatus.FAILED,
            error_code="authentication",
            error_message="HTTP 401",
        ),
        CollectionResult(source="huggingface", status=CollectionStatus.EMPTY),
        CollectionResult(source="threads", status=CollectionStatus.NOT_CONFIGURED),
    ])
    text = str(blocks)
    assert "GitHub 3건" in text
    assert "Reddit 실패" in text
    assert "Reddit 인증 실패" in text
    assert "Hugging Face 검색 결과 없음" in text
    assert "Threads 설정 필요" in text
    assert "HTTP 401" not in text


@pytest.mark.asyncio
@respx.mock
async def test_slack_dedup_and_dry_run(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/report.db")
    store.create_all()
    route = respx.post("https://hooks.slack.test/1").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        reporter = SlackReporter(store, client, "https://hooks.slack.test/1")
        day = datetime.now(timezone.utc).date()
        assert await reporter.send(day, [], dry_run=True) is False
        assert await reporter.send(day, []) is True
        assert await reporter.send(day, []) is False
    assert route.call_count == 1
