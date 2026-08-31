import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import typer

from .config import get_settings
from .reporters import SlackReporter, build_blocks
from .service import DailyService, configure_logging
from .storage import Store
from .summarizer import Summarizer

app = typer.Typer(no_args_is_help=True)


def runtime() -> tuple[DailyService, Store, httpx.AsyncClient]:
    settings = get_settings()
    store = Store(settings.database_url)
    client = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
    return DailyService(settings, store, client), store, client


@app.command()
def init_db() -> None:
    _, store, client = runtime()
    store.create_all()
    asyncio.run(client.aclose())


@app.command()
def collect(source: str | None = None, dry_run: bool = False) -> None:
    async def run() -> None:
        service, _, client = runtime()
        try:
            typer.echo(json.dumps(await service.collect(source, dry_run), ensure_ascii=False))
        finally:
            await client.aclose()
    asyncio.run(run())


async def _make_report(hours: int, send: bool, dry_run: bool, force: bool) -> list[dict[str, object]]:
    service, store, client = runtime()
    settings = get_settings()
    try:
        items = service.recent(hours)[: settings.report_top_n]
        summarizer = Summarizer(client, settings.llm_api_key.get_secret_value() if settings.llm_api_key else None, settings.llm_base_url, settings.llm_model)
        summaries = await asyncio.gather(*(summarizer.summarize(item) for item in items))
        end = datetime.now(timezone.utc).astimezone(settings.tz)
        blocks = build_blocks(list(zip(items, summaries, strict=True)), end - timedelta(hours=hours), end)
        if send:
            reporter = SlackReporter(store, client, settings.slack_webhook_url.get_secret_value() if settings.slack_webhook_url else None)
            await reporter.send(end.date(), blocks, force=force, dry_run=dry_run)
        return blocks
    finally:
        await client.aclose()


@app.command()
def analyze(hours: int = 24) -> None:
    service, _, client = runtime()
    typer.echo(json.dumps([item.model_dump(mode="json") for item in service.recent(hours)], ensure_ascii=False))
    asyncio.run(client.aclose())


@app.command()
def preview(hours: int = 24) -> None:
    typer.echo(json.dumps(asyncio.run(_make_report(hours, False, True, False)), ensure_ascii=False, indent=2))


@app.command()
def report(hours: int = 24, dry_run: bool = False, force: bool = False) -> None:
    typer.echo(json.dumps(asyncio.run(_make_report(hours, True, dry_run, force)), ensure_ascii=False))


@app.command("daily")
def daily(dry_run: bool = False, force: bool = False) -> None:
    async def run() -> None:
        service, _, client = runtime()
        try:
            typer.echo(json.dumps(await service.collect(dry_run=dry_run), ensure_ascii=False))
        finally:
            await client.aclose()
        typer.echo(json.dumps(await _make_report(24, True, dry_run, force), ensure_ascii=False))
    asyncio.run(run())


@app.command()
def backfill(start: datetime, end: datetime, dry_run: bool = False) -> None:
    if start >= end:
        raise typer.BadParameter("start must be before end")
    typer.echo(f"backfill window accepted: {start.isoformat()}..{end.isoformat()} dry_run={dry_run}")
    collect(dry_run=dry_run)


@app.command()
def health() -> None:
    service, _, client = runtime()
    service.store.create_all()
    asyncio.run(client.aclose())
    typer.echo("ok")


configure_logging()
