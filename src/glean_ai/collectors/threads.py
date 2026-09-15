from datetime import datetime
import math

import httpx
from pydantic import HttpUrl

from ..models import Content
from .base import Collector, CollectorError


class ThreadsCollector(Collector):
    source = "threads"
    api_url = "https://graph.threads.net/v1.0/keyword_search"

    def __init__(
        self, client: httpx.AsyncClient, limit: int = 100, token: str | None = None
    ) -> None:
        super().__init__(client, limit)
        self.token = token

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        if not self.token:
            raise CollectorError("not_configured", "Threads access token is not configured")
        self.partial_errors = []
        found: dict[str, dict[str, object]] = {}
        keywords = interests.get("keywords", [])
        per_keyword = max(1, math.ceil(self.limit / max(1, len(keywords))))
        for keyword in keywords:
            after: str | None = None
            remaining = per_keyword
            try:
                while remaining > 0:
                    params: dict[str, object] = {
                        "q": keyword,
                        "search_type": "RECENT",
                        "fields": "id,text,username,permalink,timestamp,media_type",
                        "limit": min(50, remaining),
                        "access_token": self.token,
                    }
                    if after:
                        params["after"] = after
                    data = await self.get_json(self.api_url, params=params)
                    page = data.get("data", [])
                    for item in page:
                        found[str(item["id"])] = item
                    remaining -= len(page)
                    after = data.get("paging", {}).get("cursors", {}).get("after")
                    if not after or not page:
                        break
            except Exception as exc:
                self.partial_errors.append(
                    exc if isinstance(exc, CollectorError)
                    else CollectorError("transport", type(exc).__name__)
                )
        if self.partial_errors and not found:
            raise self.partial_errors[0]
        return [self._normalize(item) for item in list(found.values())[:self.limit]]

    def _normalize(self, item: dict[str, object]) -> Content:
        text = str(item.get("text") or "")
        return Content(
            source=self.source,
            external_id=str(item["id"]),
            content_type=str(item.get("media_type") or "post").lower(),
            author=str(item["username"]) if item.get("username") else None,
            title=text[:160] or "Threads 게시물",
            body=text,
            url=HttpUrl(str(item["permalink"])),
            published_at=datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00")),
            raw_metadata={"media_type": item.get("media_type")},
        )
