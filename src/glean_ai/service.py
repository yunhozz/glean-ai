import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
import structlog

from .collectors import GitHubCollector, HuggingFaceCollector, RedditCollector, RSSCollector
from .collectors.base import CollectorError
from .config import Settings
from .models import CollectionResult, CollectionStatus, Content
from .pipeline import process
from .storage import RunRow, Store

log = structlog.get_logger()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ])


class DailyService:
    def __init__(self, settings: Settings, store: Store, client: httpx.AsyncClient) -> None:
        self.settings, self.store, self.client = settings, store, client

    def collectors(self) -> dict[str, object]:
        github_token = (
            self.settings.github_token.get_secret_value() if self.settings.github_token else None
        )
        huggingface_token = (
            self.settings.huggingface_token.get_secret_value()
            if self.settings.huggingface_token else None
        )
        collectors = {
            "github": GitHubCollector(
                self.client, self.settings.source_limit, token=github_token
            ),
            "huggingface": HuggingFaceCollector(
                self.client, self.settings.source_limit, token=huggingface_token
            ),
            "reddit": RedditCollector(
                self.client,
                self.settings.source_limit,
                user_agent=self.settings.reddit_user_agent,
            ),
        }
        for feed in self.settings.rss_feeds():
            collectors[feed["id"]] = RSSCollector(
                self.client,
                feed["id"],
                feed["name"],
                feed["url"],
                self.settings.source_limit,
            )
        return collectors

    async def collect(
        self, source: str | None = None, dry_run: bool = False
    ) -> dict[str, CollectionResult]:
        interests = self.settings.interests()
        enabled = {
            "github": self.settings.github_enabled,
            "huggingface": self.settings.huggingface_enabled,
            "reddit": self.settings.reddit_enabled,
        }
        selected = self.collectors()
        enabled.update({feed["id"]: True for feed in self.settings.rss_feeds()})
        if source:
            if source not in selected:
                raise ValueError(f"unsupported or unavailable source: {source}")
            selected = {source: selected[source]}
        results = await asyncio.gather(
            *[self._collect_one(name, collector, interests, enabled[name], dry_run) for name, collector in selected.items()],
        )
        return dict(results)

    async def _collect_one(
        self,
        name: str,
        collector: object,
        interests: dict[str, list[str]],
        enabled: bool,
        dry_run: bool,
    ) -> tuple[str, CollectionResult]:
        started = datetime.now(timezone.utc)
        if not enabled:
            log.warning("source_disabled", source=name)
            result = CollectionResult(source=name, status=CollectionStatus.DISABLED)
            self._record(result, started, dry_run)
            return name, result
        try:
            contents: list[Content] = await collector.collect(interests)  # type: ignore[attr-defined]
            fetched_count = len(contents)
            contents = process(
                contents,
                interests.get("keywords", []),
                include_unmatched=isinstance(collector, RSSCollector),
            )
            inserted = 0 if dry_run else sum(self.store.upsert(item) for item in contents)
            partial_errors = collector.partial_errors  # type: ignore[attr-defined]
            status = (
                CollectionStatus.PARTIAL if partial_errors
                else CollectionStatus.SUCCESS if contents
                else CollectionStatus.EMPTY
            )
            error = partial_errors[0] if partial_errors else None
            result = CollectionResult(
                source=name,
                status=status,
                contents=contents,
                fetched_count=fetched_count,
                accepted_count=len(contents),
                error_code=error.code if error else None,
                error_message=str(error) if error else None,
            )
            self._record(result, started, dry_run)
            log.info("source_collected", source=name, status=status, inserted=inserted)
            return name, result
        except Exception as exc:
            if isinstance(exc, CollectorError):
                error_code = exc.code
                safe_error = str(exc)[:200]
            elif name == "toss_tech" and isinstance(exc, httpx.TransportError):
                error_code = "transport"
                safe_error = type(exc).__name__
            else:
                error_code = "unexpected"
                safe_error = type(exc).__name__
            status = (
                CollectionStatus.NOT_CONFIGURED
                if error_code == "not_configured" else CollectionStatus.FAILED
            )
            log.error("source_failed", source=name, error=safe_error)
            result = CollectionResult(
                source=name,
                status=status,
                error_code=error_code,
                error_message=safe_error,
            )
            self._record(result, started, dry_run)
            return name, result

    def _record(
        self, result: CollectionResult, started: datetime, dry_run: bool
    ) -> None:
        if dry_run:
            return
        with self.store.session() as session:
            session.add(RunRow(
                kind="collect",
                source=result.source,
                status=result.status,
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                error=result.error_message,
                error_code=result.error_code,
                fetched_count=result.fetched_count,
                accepted_count=result.accepted_count,
            ))

    def recent(self, hours: int = 24) -> list[Content]:
        rows = self.store.recent(datetime.now(timezone.utc) - timedelta(hours=hours))
        return [Content.model_validate({column.name: getattr(row, column.name) for column in row.__table__.columns if column.name != "id"}) for row in rows]
