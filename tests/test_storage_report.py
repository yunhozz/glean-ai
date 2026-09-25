from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re

import httpx
import pytest
import respx
from pydantic import HttpUrl

from glean_ai.reporters import SlackReporter, build_blocks, build_messages
from glean_ai.models import CollectionResult, CollectionStatus, Metrics, TopicSummary
from glean_ai.storage import ContentRow, ReportRow, Store
from glean_ai.summarizer import Summarizer


def test_storage_idempotency(tmp_path, sample):
    store = Store(f"sqlite:///{tmp_path}/test.db")
    store.create_all()
    assert store.upsert(sample) is True
    assert store.upsert(sample) is False
    rows = store.recent(datetime.now(timezone.utc) - timedelta(days=1))
    assert rows[0].title == sample.title


def test_storage_batch_upsert_is_idempotent(tmp_path, sample):
    store = Store(f"sqlite:///{tmp_path}/batch.db")
    store.create_all()
    second = deepcopy(sample)
    second.external_id = "2"
    second.url = HttpUrl("https://github.com/acme/another")

    assert store.upsert_many([sample, second]) == 2
    assert store.upsert_many([sample, second]) == 0
    with store.session() as session:
        assert session.query(ContentRow).count() == 2


def test_newly_discovered_tech_blog_post_appears_once(tmp_path, sample):
    store = Store(f"sqlite:///{tmp_path}/tech.db")
    store.create_all()
    now = datetime.now(timezone.utc)
    old_post = deepcopy(sample)
    old_post.source = "kakao_tech"
    old_post.published_at = now - timedelta(days=7)
    old_post.collected_at = now
    assert store.upsert(old_post) is True

    since = now - timedelta(days=1)
    assert store.recent(since) == []
    assert [row.external_id for row in store.recent(since, {"kakao_tech"})] == [
        old_post.external_id
    ]
    assert store.upsert(old_post) is False
    assert store.recent(now + timedelta(days=1), {"kakao_tech"}) == []


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
    assert blocks[0]["text"]["text"] == "🤖 전체 소식 · AI 기술"
    assert any(sample.title in str(block) for block in blocks)
    assert any("실무 포인트" in str(block) for block in blocks)
    assert all("왜 중요한가" not in str(block) for block in blocks)
    assert "Open source workflow automation" not in str(blocks)


def test_blocks_show_every_item_in_highlighted_format(sample):
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
    assert "📌 함께 볼 소식" not in text
    assert all(f"상세 요약 {index}" in text for index in range(5))
    assert all(f"실무 내용 {index}" in text for index in range(5))
    assert all(f"소식 {index}" in text for index in range(5))
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


def test_long_title_and_url_remain_a_working_link(sample):
    content = deepcopy(sample)
    url = "https://example.com/" + "x" * 1950
    content.url = HttpUrl(url)
    summary = TopicSummary(
        title_ko="긴제목" * 320,
        summary_ko="요약",
        areas=["개발"],
        why_important="실무 내용",
    )
    now = datetime.now(timezone.utc)
    blocks = build_messages(
        [(content, summary)], now - timedelta(days=1), now,
        platforms=[("github", "GitHub", "AI 기술")],
    )["github"]
    sections = [block["text"]["text"] for block in blocks if block["type"] == "section"]

    assert summary.title_ko in "".join(sections)
    assert any(re.search(rf"<{re.escape(url)}\|[^>]+>", section) for section in sections)


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
    ])
    text = str(blocks)
    assert "GitHub 3건" in text
    assert "Reddit 실패" in text
    assert "Reddit 인증 실패" in text
    assert "Hugging Face 검색 결과 없음" in text
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
        assert await reporter.send(day, [], force=True) is True
    assert route.call_count == 2
    with store.session() as session:
        assert session.query(ReportRow).count() == 1


@pytest.mark.asyncio
async def test_retry_sends_only_platforms_not_delivered_before_failure(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/partial-report.db")
    store.create_all()
    messages = {
        source: [{"type": "header", "text": {"type": "plain_text", "text": source}}]
        for source in ("github", "reddit")
    }
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        source = json.loads(request.content)["blocks"][0]["text"]["text"]
        calls.append(source)
        return httpx.Response(500 if len(calls) == 2 else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        reporter = SlackReporter(store, client, "https://hooks.slack.test/1")
        day = datetime.now(timezone.utc).date()
        with pytest.raises(httpx.HTTPStatusError):
            await reporter.send(day, messages)
        assert await reporter.send(day, messages) is True

    assert calls == ["github", "reddit", "reddit"]
    with store.session() as session:
        assert session.query(ReportRow).count() == 1


@pytest.mark.asyncio
async def test_forced_retry_resumes_platforms_missing_from_that_attempt(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/forced-report.db")
    store.create_all()
    messages = {
        source: [{"type": "header", "text": {"type": "plain_text", "text": source}}]
        for source in ("github", "reddit")
    }
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        source = json.loads(request.content)["blocks"][0]["text"]["text"]
        calls.append(source)
        return httpx.Response(500 if len(calls) == 4 else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        reporter = SlackReporter(store, client, "https://hooks.slack.test/1")
        day = datetime.now(timezone.utc).date()
        assert await reporter.send(day, messages) is True
        with pytest.raises(httpx.HTTPStatusError):
            await reporter.send(day, messages, force=True)
        assert await reporter.send(day, messages) is False
        assert await reporter.send(day, messages, force=True) is True

    assert calls == ["github", "reddit", "github", "reddit", "reddit"]


@pytest.mark.asyncio
async def test_many_items_keep_every_link_in_one_message_per_platform(tmp_path, sample):
    rows = []
    for index in range(100):
        content = deepcopy(sample)
        content.external_id = str(index)
        content.url = HttpUrl(f"https://github.com/acme/project-{index}")
        summary = TopicSummary(
            title_ko=f"소식 {index}",
            summary_ko="요약 " * 150,
            areas=["개발"],
            why_important="실무 " * 150,
        )
        rows.append((content, summary))
    now = datetime.now(timezone.utc)
    messages = build_messages(
        rows,
        now - timedelta(days=1),
        now,
        [
            CollectionResult(source="github", status=CollectionStatus.SUCCESS, accepted_count=100),
            CollectionResult(source="reddit", status=CollectionStatus.FAILED),
        ],
        platforms=[("github", "GitHub", "AI 기술"), ("reddit", "Reddit", "AI 기술")],
    )
    github_text = str(messages["github"])
    assert all(
        f"<{content.url}|소식 {index}>" in github_text
        for index, (content, _) in enumerate(rows)
    )
    assert len(messages["github"]) <= 50
    assert all(
        len(block["text"]["text"]) <= 2900
        for block in messages["github"]
        if block["type"] == "section"
    )
    assert "Reddit 실패" in str(messages["reddit"])

    delivered = []

    def respond(request: httpx.Request) -> httpx.Response:
        delivered.append(json.loads(request.content)["blocks"][0]["text"]["text"])
        return httpx.Response(200)

    store = Store(f"sqlite:///{tmp_path}/platforms.db")
    store.create_all()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        reporter = SlackReporter(store, client, "https://hooks.slack.test/1")
        assert await reporter.send(now.date(), messages) is True

    assert delivered == ["🤖 GitHub · AI 기술", "🤖 Reddit · AI 기술"]
