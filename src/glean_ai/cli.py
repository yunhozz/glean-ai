import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog
import typer

from .config import get_settings
from .models import CollectionResult, CollectionStatus, Content, TopicSummary
from .pipeline import canonical_url, rank_ai_news, select_report_items, select_technology_report_items
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
) -> dict[str, list[dict[str, Any]]]:
    service, store, client = runtime()
    settings = get_settings()
    try:
        platforms = [
            (
                feed["id"],
                feed["name"],
                "AI 뉴스" if feed["group"] == "news" else "AI 기술",
            )
            for feed in settings.rss_feeds()
        ]
        platforms.extend([
            ("github", "GitHub", "AI 기술"),
            ("huggingface", "Hugging Face", "AI 기술"),
            ("reddit", "Reddit", "AI 기술"),
        ])
        source_ids = {source for source, _, _ in platforms}
        recent_items = [
            item for item in service.recent(hours) if item.source in source_ids
        ]
        if collection_results:
            github_result = collection_results.get("github")
            if github_result is not None:
                recent_items = [item for item in recent_items if item.source != "github"]
                if github_result.status in {CollectionStatus.SUCCESS, CollectionStatus.PARTIAL}:
                    recent_items.extend(github_result.contents)
            reddit_result = collection_results.get("reddit")
            if reddit_result and reddit_result.status in {
                CollectionStatus.SUCCESS, CollectionStatus.PARTIAL, CollectionStatus.EMPTY,
            }:
                recent_items = [item for item in recent_items if item.source != "reddit"]
                recent_items.extend(reddit_result.contents)
            if dry_run:
                current_items = [
                    item for result in collection_results.values()
                    for item in result.contents
                    if item.source in source_ids and item.source not in {"reddit", "github"}
                ]
                current_ids = {(item.source, item.external_id) for item in current_items}
                recent_items = [
                    item for item in recent_items
                    if (item.source, item.external_id) not in current_ids
                ]
                known_urls = {canonical_url(str(item.url)) for item in recent_items}
                recent_items.extend(
                    item for item in current_items
                    if canonical_url(str(item.url)) not in known_urls
                )
        fallback_sources: set[str] = set()
        for source, _, group in platforms:
            if group == "AI 기술" and source not in {"github", "huggingface", "reddit"}:
                if not any(item.source == source for item in recent_items):
                    fallback_sources.add(source)
                    recent_items.extend(service.latest_for_source(source, 2))
        items = sorted(recent_items, key=lambda item: item.final_score, reverse=True)
        source_groups = {source: group for source, _, group in platforms}
        selected_items: list[Content] = []
        for group_name in ("AI 뉴스", "AI 기술"):
            group_sources = [
                source for source, _, group in platforms if group == group_name
            ]
            group_items = [
                item for item in items if source_groups[item.source] == group_name
            ]
            if group_name == "AI 뉴스":
                group_items = rank_ai_news(group_items)
                group_selected = select_report_items(
                    group_items, limit=28, preferred_sources=group_sources, max_per_source=2,
                )
            else:
                group_selected = select_technology_report_items(
                    group_items,
                    [source for source in group_sources if source not in {"github", "huggingface", "reddit"}],
                    fallback_sources,
                )
            selected_items.extend(group_selected)
            if group_name == "AI 뉴스":
                for item in group_selected:
                    log.info(
                        "news_item_selected",
                        source=item.source,
                        title=item.title,
                        rank=item.final_score,
                        evidence=item.raw_metadata.get("news_rank_evidence", {}),
                    )
        items = selected_items
        summarizer = Summarizer(client, settings.llm_api_key.get_secret_value() if settings.llm_api_key else None, settings.llm_base_url, settings.llm_model)
        semaphore = asyncio.Semaphore(10)

        async def summarize(item: Content) -> TopicSummary:
            async with semaphore:
                return await summarizer.summarize(item)

        summaries = await asyncio.gather(*(summarize(item) for item in items))
        end = datetime.now(timezone.utc).astimezone(settings.tz)
        messages = build_messages(
            list(zip(items, summaries, strict=True)),
            end - timedelta(hours=hours),
            end,
            list(collection_results.values()) if collection_results is not None else None,
            platforms=platforms,
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
