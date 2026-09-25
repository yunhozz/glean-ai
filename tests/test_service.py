import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
import respx

from glean_ai.config import Settings
from glean_ai.models import CollectionStatus, Content
from glean_ai.service import DailyService
from glean_ai.storage import Store
from glean_ai.storage import RunRow


@pytest.mark.asyncio
@respx.mock
async def test_public_sources_run_without_reddit_credentials(tmp_path):
    respx.get("https://api.github.com/search/repositories").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(
        "https://www.reddit.com/r/artificial+MachineLearning+LocalLLaMA/top/.rss?t=day"
    ).mock(
        return_value=httpx.Response(
            200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>'
        )
    )
    interests_path = tmp_path / "interests.yaml"
    interests_path.write_text(
        "keywords: [AI]\nsubreddits: [artificial, MachineLearning, LocalLLaMA]\n"
        "tech_blogs: []\n",
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'service.db'}",
        interest_config_path=interests_path,
    )
    store = Store(settings.database_url)
    store.create_all()
    async with httpx.AsyncClient() as client:
        results = await DailyService(settings, store, client).collect()
    assert results["github"].status == CollectionStatus.EMPTY
    assert results["huggingface"].status == CollectionStatus.EMPTY
    assert results["reddit"].status == CollectionStatus.EMPTY
    with store.session() as session:
        runs = session.query(RunRow).all()
    assert {run.source: run.status for run in runs} == {
        "github": "empty",
        "huggingface": "empty",
        "reddit": "empty",
    }


@pytest.mark.asyncio
@respx.mock
async def test_tech_blog_storage_does_not_block_network_loop(tmp_path):
    published = datetime.now(timezone.utc) - timedelta(days=7)
    rss = (
        "<rss><channel><item><title>Engineering update</title>"
        "<link>https://example.com/post</link>"
        f"<pubDate>{format_datetime(published)}</pubDate>"
        "</item></channel></rss>"
    )
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<title>Engineering update</title>"
        '<link href="https://example.com/atom-post" />'
        f"<updated>{published.isoformat()}</updated>"
        "</entry></feed>"
    )
    feeds = {
        "kakao_tech": ("https://tech.kakao.com/feed/", rss),
        "naver_d2": ("https://d2.naver.com/d2.atom", atom),
        "toss_tech": ("https://toss.tech/rss.xml", rss),
    }
    for url, body in feeds.values():
        respx.get(url).mock(return_value=httpx.Response(200, text=body))
    interests_path = tmp_path / "interests.yaml"
    interests_path.write_text(
        "keywords: []\nai_news_feeds: []\ntech_blogs:\n"
        + "".join(
            f"  - id: {name}\n    name: {name}\n    url: {url}\n"
            for name, (url, _) in feeds.items()
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'blogs.db'}",
        interest_config_path=interests_path,
        github_enabled=False,
        huggingface_enabled=False,
        reddit_enabled=False,
    )

    class RecordingStore(Store):
        def __init__(self, database_url: str) -> None:
            super().__init__(database_url)
            self.upsert_threads: list[int] = []

        def upsert_many(self, contents: list[Content]) -> int:
            self.upsert_threads.append(threading.get_ident())
            return super().upsert_many(contents)

    store = RecordingStore(settings.database_url)
    store.create_all()
    event_loop_thread = threading.get_ident()
    async with httpx.AsyncClient() as client:
        service = DailyService(settings, store, client)
        results = await service.collect()
        recent_sources = {item.source for item in service.recent()}

    assert all(results[name].status == CollectionStatus.SUCCESS for name in feeds)
    assert recent_sources == set(feeds)
    assert len(store.upsert_threads) == 3
    assert all(thread_id != event_loop_thread for thread_id in store.upsert_threads)
    with store.session() as session:
        assert session.query(RunRow).count() == 6
