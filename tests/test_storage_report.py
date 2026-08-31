from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from glean_ai.reporters import SlackReporter, build_blocks
from glean_ai.storage import Store
from glean_ai.summarizer import Summarizer


def test_storage_idempotency(tmp_path, sample):
    store = Store(f"sqlite:///{tmp_path}/test.db")
    store.create_all()
    assert store.upsert(sample) is True
    assert store.upsert(sample) is False


@pytest.mark.asyncio
async def test_llm_fallback_and_blocks(sample):
    async with httpx.AsyncClient() as client:
        summary = await Summarizer(client, None, "https://example.com", "model").summarize(sample)
    blocks = build_blocks([(sample, summary)], datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc))
    assert blocks[0]["text"]["text"] == "오늘의 AI Product · Dev · Design Brief"
    assert any(sample.title in str(block) for block in blocks)


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
