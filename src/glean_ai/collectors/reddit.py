import html
import re
from datetime import datetime
from xml.etree import ElementTree

import httpx
from pydantic import HttpUrl

from ..models import Content
from .base import Collector, CollectorError

ATOM = {"atom": "http://www.w3.org/2005/Atom"}


class RedditCollector(Collector):
    source = "reddit"

    def __init__(
        self,
        client: httpx.AsyncClient,
        limit: int = 100,
        user_agent: str = "glean-ai/0.1 (contact: github.com/yunhozz/glean-ai)",
    ) -> None:
        super().__init__(client, limit)
        self.user_agent = user_agent

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        self.partial_errors = []
        subreddits = interests.get("subreddits", ["artificial"])
        feed = "+".join(subreddits)
        document = await self.get_text(
            f"https://www.reddit.com/r/{feed}/top/.rss",
            params={"t": "day"},
            headers={"User-Agent": self.user_agent},
        )
        found = {item.external_id: item for item in self._parse(document)}
        return list(found.values())[:self.limit]

    def _parse(self, document: str) -> list[Content]:
        try:
            root = ElementTree.fromstring(document)
        except ElementTree.ParseError as exc:
            raise CollectorError("invalid_response", "Invalid Reddit RSS response") from exc
        output = []
        for daily_rank, entry in enumerate(root.findall("atom:entry", ATOM), 1):
            external_id = entry.findtext("atom:id", default="", namespaces=ATOM)
            title = entry.findtext("atom:title", default="", namespaces=ATOM)
            published = entry.findtext("atom:published", namespaces=ATOM)
            link = entry.find("atom:link", ATOM)
            if not external_id or not title or not published or link is None:
                continue
            author = entry.findtext("atom:author/atom:name", namespaces=ATOM)
            content = entry.findtext("atom:content", default="", namespaces=ATOM)
            body = html.unescape(re.sub(r"<[^>]+>", " ", content))
            body = re.sub(r"\s+", " ", body).strip()
            subreddit_match = re.search(r"/r/([^/]+)/", link.attrib["href"])
            output.append(Content(
                source=self.source,
                external_id=external_id,
                content_type="post",
                author=author,
                title=title,
                body=body,
                url=HttpUrl(link.attrib["href"]),
                published_at=datetime.fromisoformat(published.replace("Z", "+00:00")),
                raw_metadata={
                    "subreddit": subreddit_match.group(1) if subreddit_match else None,
                    "daily_rank": daily_rank,
                },
            ))
        return output
