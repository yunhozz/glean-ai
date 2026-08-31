from datetime import datetime, timezone

from pydantic import HttpUrl

from ..models import Content, Metrics
from .base import Collector


class RedditCollector(Collector):
    source = "reddit"

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        subreddits = interests.get("subreddits", ["artificial"])
        data = await self.get_json(
            f"https://www.reddit.com/r/{'+'.join(subreddits)}/new.json",
            params={"limit": self.limit},
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
