from datetime import datetime
import re
from typing import Any

import httpx

from pydantic import HttpUrl

from ..models import Content, Metrics
from .base import Collector


MIN_LIKES = 50
MIN_DOWNLOADS = 5_000

AI_PIPELINE_TAGS = {
    "audio-classification",
    "audio-to-audio",
    "automatic-speech-recognition",
    "conversational",
    "depth-estimation",
    "document-question-answering",
    "feature-extraction",
    "fill-mask",
    "image-classification",
    "image-segmentation",
    "image-text-to-text",
    "image-to-image",
    "image-to-text",
    "object-detection",
    "question-answering",
    "sentence-similarity",
    "summarization",
    "text-classification",
    "text-generation",
    "text-ranking",
    "text-to-audio",
    "text-to-image",
    "text-to-speech",
    "text-to-video",
    "token-classification",
    "translation",
    "video-classification",
    "visual-question-answering",
    "zero-shot-classification",
}

SENSITIVE_MARKERS = (
    "nsfw", "not safe for work", "adult", "porn", "sexual", "erotic", "hentai", "sexgod",
)


def _is_relevant(item: dict[str, Any], interests: dict[str, list[str]]) -> bool:
    tags = item.get("tags") or []
    pipeline_tag = item.get("pipeline_tag") or ""
    searchable = " ".join([item.get("id", ""), pipeline_tag, *tags]).lower()
    return pipeline_tag in AI_PIPELINE_TAGS or any(
        keyword.lower() in searchable for keyword in interests.get("keywords", [])
    )


def _is_sensitive(item: dict[str, Any]) -> bool:
    tags = item.get("tags") or []
    searchable = re.sub(
        r"[^a-z0-9]+", " ", " ".join([item.get("id", ""), *tags]).lower()
    )
    return any(marker in searchable for marker in SENSITIVE_MARKERS)


class HuggingFaceCollector(Collector):
    source = "huggingface"

    def __init__(
        self, client: httpx.AsyncClient, limit: int = 100, token: str | None = None
    ) -> None:
        super().__init__(client, limit)
        self.token = token

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        data = await self.get_json(
            "https://huggingface.co/api/models",
            params={
                "sort": "trendingScore",
                "direction": -1,
                "limit": self.limit,
                "expand": [
                    "author", "downloads", "lastModified", "likes", "pipeline_tag",
                    "tags", "trendingScore",
                ],
            },
            headers=headers,
        )
        contents: list[Content] = []
        for item in data:
            if not _is_relevant(item, interests) or _is_sensitive(item):
                continue

            likes = item.get("likes") or 0
            downloads = item.get("downloads") or 0
            trending_score = item.get("trendingScore")
            if not (
                isinstance(trending_score, (int, float))
                and not isinstance(trending_score, bool)
                and trending_score > 0
                and (likes >= MIN_LIKES or downloads >= MIN_DOWNLOADS)
            ):
                continue

            tags = item.get("tags") or []
            pipeline_tag = item.get("pipeline_tag")
            last_modified = item.get("lastModified")
            if not last_modified:
                continue

            contents.append(Content(
                source=self.source,
                external_id=item["id"],
                content_type="model",
                author=item.get("author"),
                title=item["id"],
                body=" ".join(["model", *tags, *([pipeline_tag] if pipeline_tag else [])]),
                url=HttpUrl(f"https://huggingface.co/{item['id']}"),
                published_at=datetime.fromisoformat(last_modified.replace("Z", "+00:00")),
                metrics=Metrics(likes=likes, downloads=downloads),
                raw_metadata={
                    "tags": tags,
                    "pipeline_tag": pipeline_tag,
                    "trending_score": trending_score,
                },
            ))
        return contents
