import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from ..models import Content
from .base import Collector, CollectorError

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"


def _text(entry: ElementTree.Element, *paths: str) -> str | None:
    for path in paths:
        element = entry.find(path)
        if element is not None:
            value = " ".join(element.itertext()).strip()
            if value:
                return value
    return None


def _clean_markup(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _parse_date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class RSSCollector(Collector):
    source = "rss"

    def __init__(
        self,
        client: httpx.AsyncClient,
        source: str,
        name: str,
        url: str,
        limit: int = 100,
    ) -> None:
        super().__init__(client, limit)
        self.source = source
        self.name = name
        self.url = url

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        self.partial_errors = []
        document = await self.get_text(
            self.url,
            retry_rate_limited=self.source != "venturebeat_ai",
            follow_redirects=True,
            headers={
                "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml",
                "User-Agent": "glean-ai/0.1 (contact: github.com/yunhozz/glean-ai)",
            },
        )
        return self._parse(document)[:self.limit]

    def _parse(self, document: str) -> list[Content]:
        try:
            root = ElementTree.fromstring(document)
        except ElementTree.ParseError as exc:
            raise CollectorError("invalid_response", "Invalid RSS/Atom response") from exc

        entries = root.findall(f".//{ATOM}entry") or root.findall(".//item")
        output = []
        for entry in entries:
            title = _text(entry, f"{ATOM}title", "title")
            if not title:
                continue

            atom_links = entry.findall(f"{ATOM}link")
            link = next(
                (
                    item.get("href") for item in atom_links
                    if item.get("href") and item.get("rel", "alternate") == "alternate"
                ),
                None,
            )
            link = link or _text(entry, "link")
            published = _text(
                entry,
                f"{ATOM}published", f"{ATOM}updated", "pubDate", "published", "updated",
                f"{DC}date",
            )
            if not link or not published:
                continue
            try:
                published_at = _parse_date(published)
            except (ValueError, TypeError, OverflowError):
                continue

            external_id = _text(entry, f"{ATOM}id", "guid") or link
            author = _text(
                entry, f"{ATOM}author/{ATOM}name", "author", f"{DC}creator"
            )
            body = _text(
                entry,
                f"{ATOM}summary",
                "description",
                f"{CONTENT}encoded",
                f"{ATOM}content",
            ) or ""
            output.append(Content(
                source=self.source,
                external_id=external_id,
                content_type="article",
                author=author,
                title=_clean_markup(title),
                body=_clean_markup(body)[:4000],
                url=link,
                published_at=published_at,
                raw_metadata={"publisher": self.name},
            ))
        return output
