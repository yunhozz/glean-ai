from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import HttpUrl

from ..models import Content, Metrics
from .base import Collector, CollectorError


class HackerNewsCollector(Collector):
    source = "hacker_news_ai"
    endpoint = "https://hn.algolia.com/api/v1/search_by_date"

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        self.partial_errors = []
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        hits: dict[str, dict[str, Any]] = {}
        queries = interests.get("keywords", []) or ["artificial intelligence"]

        for query in queries:
            try:
                data = await self.get_json(
                    self.endpoint,
                    params={
                        "query": query,
                        "tags": "story",
                        "numericFilters": f"created_at_i>{int(since.timestamp())}",
                        "hitsPerPage": min(self.limit, 100),
                    },
                )
                for hit in data.get("hits", []):
                    object_id = hit.get("objectID")
                    if object_id:
                        hits[str(object_id)] = hit
            except Exception as exc:
                self.partial_errors.append(
                    exc if isinstance(exc, CollectorError)
                    else CollectorError("transport", type(exc).__name__)
                )

        if self.partial_errors and not hits:
            raise self.partial_errors[0]

        ranked = sorted(
            hits.values(),
            key=lambda hit: (hit.get("points") or 0) + (hit.get("num_comments") or 0) * 2,
            reverse=True,
        )[:self.limit]
        contents: list[Content] = []
        for rank, hit in enumerate(ranked, start=1):
            object_id = str(hit["objectID"])
            title = hit.get("title")
            created_at = hit.get("created_at")
            if not title or not created_at:
                continue
            item_url = f"https://news.ycombinator.com/item?id={object_id}"
            contents.append(Content(
                source=self.source,
                external_id=item_url,
                content_type="article",
                author=hit.get("author"),
                title=title,
                body=hit.get("story_text") or "",
                url=HttpUrl(hit.get("url") or item_url),
                published_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
                metrics=Metrics(
                    likes=hit.get("points") or 0,
                    comments=hit.get("num_comments") or 0,
                ),
                raw_metadata={
                    "publisher": "Hacker News (AI)",
                    "native_popularity_period": "24h",
                    "native_rank": rank,
                    "date_basis": "published",
                },
            ))
        return contents
