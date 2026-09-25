import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog
import typer

from .config import get_settings
from .models import CollectionResult, CollectionStatus, Content, TopicSummary
from .pipeline import canonical_url
from .reporters import SlackReporter, build_messages
from .service import DailyService, configure_logging
from .storage import Store
from .summarizer import Summarizer

app = typer.Typer(no_args_is_help=True)
log = structlog.get_logger()


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
            results = await service.collect(source, dry_run)
            typer.echo(json.dumps(
                {name: result.model_dump(mode="json", exclude={"contents"}) for name, result in results.items()},
                ensure_ascii=False,
            ))
        finally:
            await client.aclose()
    asyncio.run(run())


async def _make_report(
    hours: int,
    send: bool,
    dry_run: bool,
    force: bool,
    collection_results: dict[str, CollectionResult] | None = None,
) -> dict[str, list[list[dict[str, Any]]]]:
    service, store, client = runtime()
    settings = get_settings()
    try:
        recent_items = service.recent(hours)
        if dry_run and collection_results:
            known_ids = {(item.source, item.external_id) for item in recent_items}
            known_urls = {canonical_url(str(item.url)) for item in recent_items}
            recent_items.extend(
                item
                for result in collection_results.values()
                for item in result.contents
                if (item.source, item.external_id) not in known_ids
                and canonical_url(str(item.url)) not in known_urls
            )
        items = sorted(recent_items, key=lambda item: item.final_score, reverse=True)
        summarizer = Summarizer(client, settings.llm_api_key.get_secret_value() if settings.llm_api_key else None, settings.llm_base_url, settings.llm_model)
        semaphore = asyncio.Semaphore(10)

        async def summarize(item: Content) -> TopicSummary:
            async with semaphore:
                return await summarizer.summarize(item)

        summaries = await asyncio.gather(*(summarize(item) for item in items))
        end = datetime.now(timezone.utc).astimezone(settings.tz)
        news_sources = {
            feed["id"] for feed in settings.rss_feeds() if feed["group"] == "news"
        }
        messages = build_messages(
            list(zip(items, summaries, strict=True)),
            end - timedelta(hours=hours),
            end,
            list(collection_results.values()) if collection_results is not None else None,
            news_sources,
        )
        if send:
            reporter = SlackReporter(store, client, settings.slack_webhook_url.get_secret_value() if settings.slack_webhook_url else None)
            sent = await reporter.send(end.date(), messages, force=force, dry_run=dry_run)
            log.info(
                "slack_report_result",
                status="sent" if sent else "dry_run" if dry_run else "already_sent",
                report_date=end.date().isoformat(),
                force=force,
            )
        return messages
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
            results = await service.collect(dry_run=dry_run)
            typer.echo(json.dumps(
                {name: result.model_dump(mode="json", exclude={"contents"}) for name, result in results.items()},
                ensure_ascii=False,
            ))
        finally:
            await client.aclose()
        unavailable = {
            CollectionStatus.FAILED,
            CollectionStatus.DISABLED,
            CollectionStatus.NOT_CONFIGURED,
        }
        if all(result.status in unavailable for result in results.values()):
            raise RuntimeError("all collection sources are unavailable")
        typer.echo(json.dumps(
            await _make_report(24, True, dry_run, force, results), ensure_ascii=False
        ))
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
