import asyncio
import json

import httpx
import pytest
from pydantic import HttpUrl
from datetime import datetime, timedelta, timezone

from glean_ai.service import DailyService
from glean_ai.storage import Store

from glean_ai import cli
from glean_ai.config import Settings
from glean_ai.models import CollectionResult, CollectionStatus


def test_daily_reports_statuses_when_every_source_is_unavailable(monkeypatch, capsys):
    results = {
        "github": CollectionResult(source="github", status=CollectionStatus.FAILED),
        "reddit": CollectionResult(source="reddit", status=CollectionStatus.NOT_CONFIGURED),
    }

    class Service:
        async def collect(self, dry_run: bool = False):
            return results

    class Client:
        async def aclose(self):
            pass

    async def report(hours, send, dry_run, force, collection_results):
        return {
            source: result.status.value
            for source, result in collection_results.items()
        }

    monkeypatch.setattr(cli, "runtime", lambda: (Service(), None, Client()))
    monkeypatch.setattr(cli, "_make_report", report)

    cli.daily()

    output = capsys.readouterr().out.splitlines()
    assert json.loads(output[-1]) == {
        "github": "failed",
        "reddit": "not_configured",
    }


def test_report_groups_sources_into_news_and_technology_messages(monkeypatch, tmp_path):
    config_path = tmp_path / "interests.yaml"
    config_path.write_text(
        "ai_news_feeds:\n"
        "  - id: news_one\n    name: News One\n    url: https://example.com/news.xml\n"
        "tech_blogs:\n"
        "  - id: tech_one\n    name: Tech One\n    url: https://example.com/tech.xml\n",
        encoding="utf-8",
    )
    settings = Settings(interest_config_path=config_path)

    class Service:
        def recent(self, hours):
            return []

        def latest_for_source(self, source, limit=2):
            return []

    client = httpx.AsyncClient()
    monkeypatch.setattr(cli, "runtime", lambda: (Service(), None, client))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    messages = asyncio.run(cli._make_report(24, False, True, False))

    assert list(messages) == ["AI 뉴스", "AI 기술"]
    assert "*News One*" in str(messages["AI 뉴스"])
    assert all(
        f"*{name}*" in str(messages["AI 기술"])
        for name in ("Tech One", "GitHub", "Hugging Face", "Reddit")
    )


def blog_runtime(monkeypatch, tmp_path):
    config = tmp_path / "interests.yaml"
    config.write_text(
        "ai_news_feeds: []\ntech_blogs:\n"
        "  - id: tech_one\n    name: Tech One\n    url: https://example.com/feed\n"
    )
    settings = Settings(interest_config_path=config)
    store = Store(f"sqlite:///{tmp_path / 'report.db'}")
    store.create_all()
    client = httpx.AsyncClient()
    service = DailyService(settings, store, client)
    monkeypatch.setattr(cli, "runtime", lambda: (service, store, client))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return service, store


@pytest.mark.parametrize("primary", ["none", "recent", "collected"])
def test_blog_fallback_respects_primary_candidates(monkeypatch, tmp_path, sample, primary):
    service, store = blog_runtime(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    for index in range(3):
        item = sample.model_copy(deep=True)
        item.source, item.external_id = "tech_one", str(index)
        item.title = f"Blog post {index}"
        item.url = HttpUrl(f"https://example.com/blog/{index}")
        item.published_at = now - timedelta(days=index + 3)
        item.collected_at = now - timedelta(days=5)
        item.final_score = index * 20
        if index == 2 and primary == "recent":
            item.published_at = now
        if index == 2 and primary == "collected":
            item.collected_at = now
        store.upsert(item)
    assert len(service.recent()) == (0 if primary == "none" else 1)
    messages = asyncio.run(cli._make_report(24, False, False, False))
    text = str(messages["AI 기술"])
    expected = [0, 1] if primary == "none" else [2]
    for index in range(3):
        assert (f"https://example.com/blog/{index}" in text) == (index in expected)
    if primary == "none":
        assert text.index("https://example.com/blog/0") < text.index("https://example.com/blog/1")


def test_blog_fallback_remains_available_next_day(monkeypatch, tmp_path, sample):
    service, store = blog_runtime(monkeypatch, tmp_path)
    item = sample.model_copy(deep=True)
    item.source = "tech_one"
    item.published_at -= timedelta(days=10)
    item.collected_at -= timedelta(days=2)
    store.upsert(item)
    assert service.recent() == []
    latest = service.latest_for_source("tech_one")
    assert [content.external_id for content in latest] == [item.external_id]
    assert latest[0].published_at.tzinfo == timezone.utc
    messages = asyncio.run(cli._make_report(24, False, False, False))
    assert str(item.url) in str(messages["AI 기술"])


@pytest.mark.parametrize("status", [CollectionStatus.SUCCESS, CollectionStatus.PARTIAL, CollectionStatus.EMPTY, CollectionStatus.FAILED, CollectionStatus.DISABLED])
@pytest.mark.parametrize("dry_run", [True, False])
def test_current_reddit_result_replaces_cached_candidates(monkeypatch, tmp_path, sample, status, dry_run):
    _, store = blog_runtime(monkeypatch, tmp_path)
    cached = sample.model_copy(deep=True)
    cached.source, cached.external_id = "reddit", "cached"
    cached.url = HttpUrl("https://example.com/cached")
    cached.raw_metadata = {"daily_rank": 1}
    cached.final_score = 100
    store.upsert(cached)
    current = cached.model_copy(deep=True)
    current.external_id = "current"
    current.url = HttpUrl("https://example.com/current")
    current.raw_metadata = {"daily_rank": 2}
    current.final_score = 0
    replacement_status = status in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL, CollectionStatus.EMPTY)
    results = {"reddit": CollectionResult(source="reddit", status=status, contents=[current] if status in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL) else [])}
    messages = asyncio.run(cli._make_report(24, False, dry_run, False, results))
    text = str(messages["AI 기술"])
    assert (str(cached.url) in text) == (not replacement_status)
    assert (str(current.url) in text) == (status in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL))
    assert [row.external_id for row in store.recent(datetime.now(timezone.utc) - timedelta(days=1))] == ["cached"]


def test_dry_run_replaces_same_id_with_current_reddit(monkeypatch, tmp_path, sample):
    _, store = blog_runtime(monkeypatch, tmp_path)
    items = []
    for rank in (1, 2):
        item = sample.model_copy(deep=True)
        item.source, item.external_id = "reddit", f"p{rank}"
        item.title = f"Stored rank {rank}"
        item.url = HttpUrl(f"https://example.com/p{rank}")
        item.raw_metadata = {"daily_rank": rank}
        item.final_score = rank * 40
        store.upsert(item)
        current = item.model_copy(deep=True)
        current.raw_metadata = {"daily_rank": 3 - rank}
        current.title = f"Current post {rank}"
        items.append(current)
    result = CollectionResult(source="reddit", status=CollectionStatus.SUCCESS, contents=items)
    messages = asyncio.run(cli._make_report(24, False, True, False, {"reddit": result}))
    text = str(messages["AI 기술"])
    assert "Stored rank" not in text
    assert text.index("https://example.com/p2") < text.index("https://example.com/p1")
    rows = store.recent(datetime.now(timezone.utc) - timedelta(days=1))
    assert {row.external_id: row.raw_metadata["daily_rank"] for row in rows} == {"p1": 1, "p2": 2}


def test_twenty_four_technology_items_fit_slack(monkeypatch, tmp_path, sample):
    config = tmp_path / "interests.yaml"
    blogs = [f"blog_{index}" for index in range(6)]
    config.write_text("ai_news_feeds: []\ntech_blogs:\n" + "".join(
        f"  - id: {source}\n    name: {source}\n    url: https://example.com/{source}/feed\n"
        for source in blogs
    ))
    settings = Settings(interest_config_path=config)
    store = Store(f"sqlite:///{tmp_path / 'full.db'}")
    store.create_all()
    urls = []
    github_items = []
    for source in [*blogs, "github", "huggingface", "reddit"]:
        for rank in range(1, (5 if source in {"github", "huggingface"} else 2) + 1):
            item = sample.model_copy(deep=True)
            item.source, item.external_id = source, f"{source}-{rank}"
            item.title = f"{source} item {rank}"
            item.url = HttpUrl(f"https://example.com/{source}/{rank}")
            item.final_score = rank * 20 if source == "reddit" else 100 - rank
            if source == "reddit":
                item.raw_metadata = {"daily_rank": rank}
            if source == "github":
                github_items.append(item)
            else:
                store.upsert(item)
            urls.append(str(item.url))
    store.persist_github_collection(
        github_items, CollectionResult(source="github", status="success", contents=github_items),
        datetime.now(timezone.utc),
    )
    client = httpx.AsyncClient()
    service = DailyService(settings, store, client)
    monkeypatch.setattr(cli, "runtime", lambda: (service, store, client))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    messages = asyncio.run(cli._make_report(24, False, False, False))
    blocks = messages["AI 기술"]
    text = str(blocks)
    assert len(urls) == 24
    assert all(text.count(url + "|") == 1 for url in urls)
    assert text.index("https://example.com/reddit/1") < text.index("https://example.com/reddit/2")
    assert len(blocks) <= 50
    assert all(len(block["text"]["text"]) <= 2900 for block in blocks if block["type"] == "section")


def test_news_non_dry_run_keeps_recent_candidate_semantics(monkeypatch, tmp_path, sample):
    config = tmp_path / "interests.yaml"
    config.write_text(
        "tech_blogs: []\nai_news_feeds:\n"
        "  - id: news_one\n    name: News One\n    url: https://example.com/news/feed\n"
    )
    settings = Settings(interest_config_path=config)
    store = Store(f"sqlite:///{tmp_path / 'news.db'}")
    store.create_all()
    item = sample.model_copy(deep=True)
    item.source = "news_one"
    item.published_at -= timedelta(days=10)
    store.upsert(item)
    client = httpx.AsyncClient()
    service = DailyService(settings, store, client)
    monkeypatch.setattr(cli, "runtime", lambda: (service, store, client))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    result = CollectionResult(source="news_one", status=CollectionStatus.SUCCESS, contents=[item])
    messages = asyncio.run(cli._make_report(24, False, False, False, {"news_one": result}))
    assert str(item.url) not in str(messages["AI 뉴스"])


@pytest.mark.parametrize('status', ['success', 'empty', 'failed', 'disabled'])
def test_report_current_github_result_replaces_saved_candidates(monkeypatch, sample, status):
    from glean_ai.models import CollectionResult
    old = sample.model_copy(deep=True)
    old.title = 'Old candidate'
    current = sample.model_copy(deep=True)
    current.title = 'Current candidate'
    class Service:
        def recent(self, hours):
            return [old]
        def latest_for_source(self, source, limit=2):
            return []
    client = httpx.AsyncClient()
    monkeypatch.setattr(cli, 'runtime', lambda: (Service(), None, client))
    monkeypatch.setattr(cli, 'get_settings', lambda: Settings())
    contents = [current] if status == 'success' else []
    messages = asyncio.run(cli._make_report(24, False, True, False, {'github': CollectionResult(source='github', status=status, contents=contents)}))
    text = str(messages)
    assert 'Old candidate' not in text
    assert ('Current candidate' in text) == (status == 'success')
