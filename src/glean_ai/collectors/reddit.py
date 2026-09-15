from datetime import datetime, timezone

import httpx
from pydantic import HttpUrl

from ..models import Content, Metrics
from .base import Collector, CollectorError


class RedditCollector(Collector):
    source = "reddit"

    def __init__(
        self,
        client: httpx.AsyncClient,
        limit: int = 100,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_agent: str = "glean-ai/0.1",
    ) -> None:
        super().__init__(client, limit)
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent

    async def access_token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise CollectorError("not_configured", "Reddit credentials are not configured")
        data = await self.post_json(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=httpx.BasicAuth(self.client_id, self.client_secret),
            headers={"User-Agent": self.user_agent},
        )
        try:
            token = data["access_token"]
        except (TypeError, KeyError) as exc:
            raise CollectorError("invalid_response", "Reddit token response is invalid") from exc
        return str(token)

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        token = await self.access_token()
        subreddits = interests.get("subreddits", ["artificial"])
        data = await self.get_json(
            f"https://oauth.reddit.com/r/{'+'.join(subreddits)}/new",
            params={"limit": self.limit},
            headers={"Authorization": f"Bearer {token}", "User-Agent": self.user_agent},
        )
        result = []
        for child in data.get("data", {}).get("children", []):
            item = child["data"]
            if item.get("removed_by_category") or item.get("selftext") in {"[removed]", "[deleted]"}:
                continue
            result.append(Content(
                source=self.source, external_id=item["id"], content_type="post",
                author=item.get("author"), title=item["title"], body=item.get("selftext", ""),
                url=HttpUrl(f"https://reddit.com{item['permalink']}"),
                published_at=datetime.fromtimestamp(item["created_utc"], tz=timezone.utc),
                metrics=Metrics(likes=item.get("score", 0), comments=item.get("num_comments", 0)),
                raw_metadata={"subreddit": item["subreddit"], "crosspost_parent": item.get("crosspost_parent")},
            ))
        return result
