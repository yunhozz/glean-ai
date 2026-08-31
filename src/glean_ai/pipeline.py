import hashlib
import math
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Content

CATEGORY_RULES = {
    "기획/AI 제품 전략": ("product", "strategy", "roadmap", "제품", "전략"),
    "기획/워크플로 자동화": ("workflow", "automation", "productivity", "자동화"),
    "개발/모델": ("model", "llm", "transformer", "모델"),
    "개발/에이전트": ("agent", "에이전트"),
    "개발/RAG": ("rag", "retrieval"),
    "개발/MCP": ("mcp", "model context protocol"),
    "개발/API·SDK": ("api", "sdk"),
    "개발/오픈소스": ("open source", "github", "오픈소스"),
    "개발/평가·보안": ("eval", "benchmark", "security", "평가", "보안"),
    "개발/인프라·배포": ("deploy", "inference", "infra", "배포"),
    "디자인/생성형 UI": ("generative ui", "ui generation", "생성형 ui"),
    "디자인/이미지·영상": ("image", "video", "diffusion", "이미지", "영상"),
    "디자인/프로토타이핑": ("prototype", "prototyping", "프로토타입"),
    "디자인/디자인 도구": ("design tool", "figma", "디자인 도구"),
    "디자인/UX 패턴": ("ux", "interaction", "사용자 경험"),
    "디자인/브랜드·콘텐츠": ("brand", "content creation", "브랜드"),
}


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def normalized_text(content: Content) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", " ", f"{content.title} {content.body}".lower()).strip()


def deduplicate(items: list[Content], threshold: float = 0.88) -> list[Content]:
    output: list[Content] = []
    source_ids: set[tuple[str, str]] = set()
    urls: set[str] = set()
    texts: list[str] = []
    for item in items:
        key, url, text = (item.source, item.external_id), canonical_url(str(item.url)), normalized_text(item)
        if key in source_ids or url in urls or any(SequenceMatcher(None, text, old).ratio() >= threshold for old in texts):
            continue
        source_ids.add(key)
        urls.add(url)
        texts.append(text)
        item.topic_id = hashlib.sha256(text.encode()).hexdigest()[:16]
        output.append(item)
    return output


def classify(content: Content, keywords: list[str]) -> Content:
    text = normalized_text(content)
    content.matched_keywords = [word for word in keywords if word.lower() in text]
    content.categories = [name for name, terms in CATEGORY_RULES.items() if any(term in text for term in terms)]
    return content


def score(items: list[Content], now: datetime | None = None) -> list[Content]:
    now = now or datetime.now(timezone.utc)
    by_source: dict[str, list[float]] = {}
    raw: dict[int, float] = {}
    for item in items:
        metrics = item.metrics
        engagement = metrics.likes + metrics.comments * 2 + metrics.shares * 3 + metrics.stars * 2 + metrics.forks * 3 + math.log1p(metrics.views + metrics.downloads)
        raw[id(item)] = engagement
        by_source.setdefault(item.source, []).append(engagement)
    for item in items:
        values = by_source[item.source]
        maximum = max(values) or 1
        relevance = min(100, len(item.matched_keywords) * 30 + (30 if item.categories else 0))
        trend = raw[id(item)] / maximum * 100
        quality = min(100, 35 + (20 if item.author else 0) + min(len(item.body), 450) / 10)
        age_hours = max(0, (now - item.published_at).total_seconds() / 3600)
        freshness = max(0, 100 * (1 - age_hours / 168))
        item.relevance_score, item.trend_score = relevance, trend
        item.quality_score, item.freshness_score = quality, freshness
        item.final_score = round(relevance * .35 + trend * .30 + quality * .20 + freshness * .15, 2)
        item.score_reasons = {"relevance": relevance, "trend": trend, "quality": quality, "freshness": freshness}
    return sorted(items, key=lambda value: value.final_score, reverse=True)


def process(items: list[Content], keywords: list[str]) -> list[Content]:
    relevant = [classify(item, keywords) for item in deduplicate(items)]
    return score([item for item in relevant if item.matched_keywords or item.categories])
