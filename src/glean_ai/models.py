from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class Metrics(BaseModel):
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0
    stars: int = 0
    forks: int = 0
    downloads: int = 0


class Content(BaseModel):
    source: str
    external_id: str
    content_type: str
    author: str | None = None
    title: str
    body: str = ""
    url: HttpUrl
    published_at: datetime
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    language: str | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    matched_keywords: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    topic_id: str | None = None
    relevance_score: float = 0
    trend_score: float = 0
    quality_score: float = 0
    freshness_score: float = 0
    final_score: float = 0
    score_reasons: dict[str, float] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        for name in ("published_at", "collected_at"):
            value = getattr(self, name)
            if value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
            setattr(self, name, value.astimezone(timezone.utc))


class TopicSummary(BaseModel):
    title_ko: str
    summary_ko: str
    areas: list[str]
    why_important: str
    uncertainty: str | None = None
