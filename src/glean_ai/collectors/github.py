from datetime import datetime

import httpx

from ..models import Content, Metrics
from .base import Collector


class GitHubCollector(Collector):
    source = "github"

    def __init__(
        self, client: httpx.AsyncClient, limit: int = 100, token: str | None = None
    ) -> None:
        super().__init__(client, limit)
        self.token = token

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        query = " OR ".join(interests.get("keywords", ["artificial intelligence"]))
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        data = await self.get_json(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "updated", "per_page": self.limit},
            headers=headers,
        )
        return [Content(
            source=self.source, external_id=str(item["id"]), content_type="repository",
            author=item["owner"]["login"], title=item["full_name"],
            body=item.get("description") or "", url=item["html_url"],
            published_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
            language=item.get("language"),
            metrics=Metrics(stars=item["stargazers_count"], forks=item["forks_count"]),
            raw_metadata={"topics": item.get("topics", []), "open_issues": item["open_issues_count"]},
        ) for item in data.get("items", [])]
