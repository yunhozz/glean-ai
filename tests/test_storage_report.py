from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from glean_ai.reporters import SlackReporter, build_blocks
from glean_ai.models import CollectionResult, CollectionStatus
from glean_ai.storage import Store
from glean_ai.summarizer import Summarizer


def test_storage_idempotency(tmp_path, sample):
    store = Store(f"sqlite:///{tmp_path}/test.db")
    store.create_all()
    assert store.upsert(sample) is True
    assert store.upsert(sample) is False
    rows = store.recent(datetime.now(timezone.utc) - timedelta(days=1))
    assert rows[0].title == sample.title


@pytest.mark.asyncio
async def test_llm_fallback_and_blocks(sample):
    async with httpx.AsyncClient() as client:
        summary = await Summarizer(client, None, "https://example.com", "model").summarize(sample)
    blocks = build_blocks([(sample, summary)], datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc))
    assert blocks[0]["text"]["text"] == "오늘의 AI Product · Dev · Design Brief"
    assert any(sample.title in str(block) for block in blocks)
    assert any("실무 포인트" in str(block) for block in blocks)
    assert all("왜 중요한가" not in str(block) for block in blocks)
    assert "Open source workflow automation" not in str(blocks)


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
