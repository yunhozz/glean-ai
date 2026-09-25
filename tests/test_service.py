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
