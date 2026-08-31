from datetime import datetime

from pydantic import HttpUrl

from ..models import Content, Metrics
from .base import Collector


class HuggingFaceCollector(Collector):
    source = "huggingface"

    async def collect(self, interests: dict[str, list[str]]) -> list[Content]:
        data = await self.get_json(
            "https://huggingface.co/api/models",
            params={"sort": "lastModified", "direction": -1, "limit": self.limit, "full": "true"},
        )
        return [Content(
            source=self.source, external_id=item["id"], content_type="model",
            author=item.get("author"), title=item["id"], body=" ".join(item.get("tags", [])),
            url=HttpUrl(f"https://huggingface.co/{item['id']}"),
            published_at=datetime.fromisoformat(item["lastModified"].replace("Z", "+00:00")),
            metrics=Metrics(likes=item.get("likes", 0), downloads=item.get("downloads", 0)),
            raw_metadata={"tags": item.get("tags", []), "pipeline_tag": item.get("pipeline_tag")},
        ) for item in data]
