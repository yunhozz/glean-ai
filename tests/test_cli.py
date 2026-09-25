import json

from glean_ai import cli
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
