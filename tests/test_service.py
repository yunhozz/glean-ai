import httpx
import pytest
import respx

from glean_ai.config import Settings
from glean_ai.models import CollectionStatus
from glean_ai.service import DailyService
from glean_ai.storage import Store
from glean_ai.storage import RunRow


@pytest.mark.asyncio
@respx.mock
async def test_public_sources_run_without_reddit_or_threads_credentials(tmp_path):
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
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'service.db'}")
    store = Store(settings.database_url)
    store.create_all()
    async with httpx.AsyncClient() as client:
        results = await DailyService(settings, store, client).collect()
    assert results["github"].status == CollectionStatus.EMPTY
    assert results["huggingface"].status == CollectionStatus.EMPTY
    assert results["reddit"].status == CollectionStatus.EMPTY
    assert results["threads"].status == CollectionStatus.NOT_CONFIGURED
    with store.session() as session:
        runs = session.query(RunRow).all()
    assert {run.source: run.status for run in runs} == {
        "github": "empty",
        "huggingface": "empty",
        "reddit": "empty",
        "threads": "not_configured",
    }
