import asyncio
import json

import httpx

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
