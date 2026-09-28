import hashlib
import math
import re
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from itertools import combinations
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

NEWS_ACTION_TERMS = (
    "announce", "debut", "introduc", "launch", "open source", "publish", "releas",
    "roll out", "ship", "unveil", "공개", "발표", "출시", "배포", "도입",
)
NEWS_IMPACT_GROUPS = (
    ("acqui", "funding", "invest", "partnership", "revenue", "인수", "투자", "협력"),
    ("ban", "copyright", "law", "lawsuit", "policy", "regulat", "규제", "법안", "소송"),
    ("breach", "risk", "safety", "security", "vulnerab", "보안", "안전", "취약"),
    ("benchmark", "research", "study", "reasoning", "연구", "성능", "벤치마크"),
    ("agent", "api", "chip", "model", "product", "모델", "서비스", "에이전트"),
)
NEWS_ROUNDUP_TERMS = (
    "opinion", "podcast", "roundup", "this week", "weekly", "칼럼", "주간", "정리",
)
NEWS_TOPIC_STOPWORDS = {
    "about", "after", "again", "against", "also", "and", "artificial", "from", "into",
    "intelligence", "new", "news", "over", "says", "that", "the", "their", "this", "with",
    "공개", "관련", "대한", "발표", "인공", "지능", "출시",
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
        trending_score = item.raw_metadata.get("trending_score")
        if (
            item.source == "huggingface"
            and isinstance(trending_score, (int, float))
            and not isinstance(trending_score, bool)
        ):
            engagement = max(0, trending_score)
        raw[id(item)] = engagement
        by_source.setdefault(item.source, []).append(engagement)
    for item in items:
        values = by_source[item.source]
        maximum = max(values) or 1
        relevance = min(100, len(item.matched_keywords) * 30 + (30 if item.categories else 0))
        trend = raw[id(item)] / maximum * 100
        quality = min(100, 35 + (20 if item.author else 0) + min(len(item.body), 450) / 10)
        observed_at = item.collected_at if item.source == "huggingface" else item.published_at
        age_hours = max(0, (now - observed_at).total_seconds() / 3600)
        freshness = max(0, 100 * (1 - age_hours / 168))
        item.relevance_score, item.trend_score = relevance, trend
        item.quality_score, item.freshness_score = quality, freshness
        item.final_score = round(relevance * .35 + trend * .30 + quality * .20 + freshness * .15, 2)
        item.score_reasons = {"relevance": relevance, "trend": trend, "quality": quality, "freshness": freshness}
    return sorted(items, key=lambda value: value.final_score, reverse=True)


def process(
    items: list[Content], keywords: list[str], include_unmatched: bool = False
) -> list[Content]:
    relevant = [classify(item, keywords) for item in deduplicate(items)]
    return score([
        item for item in relevant
        if include_unmatched or item.matched_keywords or item.categories
    ])


def _news_topic_tokens(item: Content) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9가-힣]+", item.title.lower())
        if len(token) >= 2 and token not in NEWS_TOPIC_STOPWORDS
    }


def _has_news_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}", text) for term in terms)


def _news_coverage(items: list[Content]) -> dict[int, int]:
    sources = {id(item): {item.source} for item in items}
    tokens = {id(item): _news_topic_tokens(item) for item in items}
    for left, right in combinations(items, 2):
        if left.source == right.source:
            continue
        left_tokens, right_tokens = tokens[id(left)], tokens[id(right)]
        shared = left_tokens & right_tokens
        smaller = min(len(left_tokens), len(right_tokens))
        if len(shared) < 2 or not smaller or len(shared) / smaller < 0.5:
            continue
        sources[id(left)].add(right.source)
        sources[id(right)].add(left.source)
    return {item_id: len(source_set) for item_id, source_set in sources.items()}


def rank_ai_news(items: list[Content]) -> list[Content]:
    """Rank 24-hour news by observable attention and editorial significance."""
    coverage = _news_coverage(items)
    for item in items:
        text = normalized_text(item)
        title = item.title.lower()
        action_signal = _has_news_term(text, NEWS_ACTION_TERMS)
        roundup_signal = _has_news_term(title, NEWS_ROUNDUP_TERMS)
        date_basis = item.raw_metadata.get("date_basis", "published")

        novelty = 60.0 if date_basis == "published" else 30.0
        novelty += 35 if action_signal else 0
        novelty -= 40 if roundup_signal else 0
        novelty = max(0.0, min(100.0, novelty))

        impact_groups = sum(
            _has_news_term(text, terms) for terms in NEWS_IMPACT_GROUPS
        )
        impact = min(100.0, impact_groups * 25.0 + (20 if action_signal else 0))

        evidence = 20.0 if item.author else 0.0
        evidence += 30 if len(item.body) >= 160 else 0
        evidence += 20 if len(item.body) >= 500 else 0
        evidence += 15 if re.search(r"\d", item.title) else 0
        evidence += 15 if date_basis == "published" else 0
        evidence = min(100.0, evidence)

        coverage_count = coverage[id(item)]
        coverage_score = min(100.0, max(0, coverage_count - 1) * 50.0)
        feed_position = item.raw_metadata.get("feed_position")
        editorial = (
            max(0.0, 100.0 - (feed_position - 1) * 10.0)
            if isinstance(feed_position, int) and not isinstance(feed_position, bool)
            else 0.0
        )
        importance = (
            novelty * 0.30
            + impact * 0.30
            + evidence * 0.20
            + coverage_score * 0.15
            + editorial * 0.05
        )
        has_native_popularity = (
            item.raw_metadata.get("native_popularity_period") == "24h"
        )
        news_rank = (
            item.trend_score * 0.65 + importance * 0.35
            if has_native_popularity else importance
        )
        item.final_score = round(news_rank, 2)
        item.score_reasons.update({
            "news_novelty": novelty,
            "news_impact": impact,
            "news_evidence": evidence,
            "news_coverage": coverage_score,
            "news_editorial": editorial,
            "news_rank": item.final_score,
        })
        item.raw_metadata["news_rank_evidence"] = {
            "cross_source_count": coverage_count,
            "date_basis": date_basis,
            "feed_position": feed_position,
            "native_popularity_24h": has_native_popularity,
            "published_at": item.published_at.isoformat(),
        }

    return sorted(
        items,
        key=lambda item: (item.final_score, item.published_at),
        reverse=True,
    )


def select_report_items(
    items: list[Content],
    limit: int,
    preferred_sources: list[str] | None = None,
    max_per_source: int | None = None,
) -> list[Content]:
    """Select a high-quality brief without letting one feed occupy the report."""
    if limit <= 0:
        return []

    ranked = sorted(items, key=lambda item: item.final_score, reverse=True)
    source_limit = (
        max_per_source
        if max_per_source is not None
        else max(1, math.ceil(limit * 0.4))
    )
    source_counts: Counter[str] = Counter()
    selected: list[Content] = []
    selected_ids: set[int] = set()

    def add(item: Content) -> None:
        selected.append(item)
        selected_ids.add(id(item))
        source_counts[item.source] += 1

    # Give the strongest candidate from each healthy source a chance to appear.
    source_candidates: list[Content] = []
    for source in preferred_sources or []:
        candidate = next((
            item for item in ranked
            if id(item) not in selected_ids
            and item.source == source
        ), None)
        if candidate:
            source_candidates.append(candidate)
    for candidate in sorted(source_candidates, key=lambda item: item.final_score, reverse=True):
        if len(selected) >= limit:
            break
        add(candidate)

    # Preserve Product, Dev and Design coverage when suitable signals exist.
    for area in ("기획", "개발", "디자인"):
        if len(selected) >= limit:
            break
        candidate = next((
            item for item in ranked
            if id(item) not in selected_ids
            and source_counts[item.source] < source_limit
            and any(category.startswith(f"{area}/") for category in item.categories)
        ), None)
        if candidate:
            add(candidate)

    for item in ranked:
        if len(selected) >= limit:
            break
        if id(item) in selected_ids or source_counts[item.source] >= source_limit:
            continue
        add(item)

    return sorted(selected, key=lambda item: item.final_score, reverse=True)
