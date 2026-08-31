import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
import structlog

from .collectors import GitHubCollector, HuggingFaceCollector, RedditCollector
from .config import Settings
from .models import Content
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
        headers: dict[str, str] = {"User-Agent": self.settings.reddit_user_agent}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token.get_secret_value()}"
        self.client.headers.update(headers)
        return {
            "github": GitHubCollector(self.client, self.settings.source_limit),
            "huggingface": HuggingFaceCollector(self.client, self.settings.source_limit),
            "reddit": RedditCollector(self.client, self.settings.source_limit),
        }

    async def collect(self, source: str | None = None, dry_run: bool = False) -> dict[str, str]:
        interests = self.settings.interests()
        enabled = {
            "github": self.settings.github_enabled,
            "huggingface": self.settings.huggingface_enabled,
            "reddit": self.settings.reddit_enabled,
        }
        selected = self.collectors()
        if source:
            if source not in selected:
                raise ValueError(f"unsupported or unavailable source: {source}")
            selected = {source: selected[source]}
        results = await asyncio.gather(
            *[self._collect_one(name, collector, interests, enabled[name], dry_run) for name, collector in selected.items()],
        )
        return dict(results)

    async def _collect_one(self, name: str, collector: object, interests: dict[str, list[str]], enabled: bool, dry_run: bool) -> tuple[str, str]:
        started = datetime.now(timezone.utc)
        if not enabled:
            log.warning("source_disabled", source=name)
            return name, "disabled"
        try:
            contents: list[Content] = await collector.collect(interests)  # type: ignore[attr-defined]
            contents = process(contents, interests.get("keywords", []))
            inserted = 0 if dry_run else sum(self.store.upsert(item) for item in contents)
            status = f"success:{inserted}/{len(contents)}"
            self._record(name, status, started, None, dry_run)
            return name, status
        except Exception as exc:
            safe_error = str(exc).replace("Authorization", "[REDACTED]")[:1000]
            log.error("source_failed", source=name, error=safe_error)
            self._record(name, "failed", started, safe_error, dry_run)
            return name, "failed"

    def _record(self, source: str, status: str, started: datetime, error: str | None, dry_run: bool) -> None:
        if dry_run:
            return
        with self.store.session() as session:
            session.add(RunRow(kind="collect", source=source, status=status, started_at=started, finished_at=datetime.now(timezone.utc), error=error))

    def recent(self, hours: int = 24) -> list[Content]:
        rows = self.store.recent(datetime.now(timezone.utc) - timedelta(hours=hours))
        return [Content.model_validate({column.name: getattr(row, column.name) for column in row.__table__.columns if column.name != "id"}) for row in rows]
