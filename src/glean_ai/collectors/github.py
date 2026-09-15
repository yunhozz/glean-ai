from datetime import datetime

import httpx

from ..models import Content, Metrics
from .base import Collector, CollectorError


def build_queries(keywords: list[str]) -> list[str]:
    terms = [f'"{word}"' if " " in word else word for word in keywords]
    queries: list[str] = []
    current: list[str] = []
    for term in terms:
        candidate = " OR ".join([*current, term])
        if current and (len(current) >= 6 or len(candidate) > 256):
            queries.append(" OR ".join(current))
            current = [term]
        else:
            current.append(term)
    if current:
        queries.append(" OR ".join(current))
    return queries or ['"artificial intelligence"']


class GitHubCollector(Collector):
    source = "github"

    def __init__(
        self, client: httpx.AsyncClient, limit: int = 100, token: str | None = None
    ) -> None:
        super().__init__(client, limit)
        self.token = token

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        self.partial_errors = []
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        items = []
        for query in build_queries(interests.get("keywords", [])):
            try:
                data = await self.get_json(
                    "https://api.github.com/search/repositories",
                    params={"q": query, "sort": "updated", "per_page": self.limit},
                    headers=headers,
                )
                items.extend(data.get("items", []))
            except Exception as exc:
                self.partial_errors.append(
                    exc if isinstance(exc, CollectorError)
                    else CollectorError("transport", type(exc).__name__)
                )
        if self.partial_errors and not items:
            raise self.partial_errors[0]
        unique = list({str(item["id"]): item for item in items}.values())[:self.limit]
        return [Content(
            source=self.source, external_id=str(item["id"]), content_type="repository",
            author=item["owner"]["login"], title=item["full_name"],
            body=item.get("description") or "", url=item["html_url"],
            published_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
            language=item.get("language"),
            metrics=Metrics(stars=item["stargazers_count"], forks=item["forks_count"]),
            raw_metadata={"topics": item.get("topics", []), "open_issues": item["open_issues_count"]},
        ) for item in unique]
