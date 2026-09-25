import asyncio
from datetime import date, datetime
from typing import Any

import httpx

from .models import CollectionResult, CollectionStatus, Content, TopicSummary
from .storage import ReportRow, Store

SOURCE_LABELS = {
    "github": "GitHub",
    "huggingface": "Hugging Face",
    "reddit": "Reddit",
    "ai_times": "AI타임스",
    "techcrunch_ai": "TechCrunch AI",
    "the_decoder": "The Decoder",
    "the_verge_ai": "The Verge AI",
    "hacker_news_ai": "Hacker News (AI)",
    "wired_ai": "Wired AI",
    "venturebeat_ai": "VentureBeat AI",
    "ars_technica_ai": "Ars Technica AI",
    "openai_news": "OpenAI News",
    "siliconangle_ai": "SiliconANGLE AI",
    "mit_technology_review_ai": "MIT Technology Review AI",
    "google_ai_blog": "Google AI Blog",
    "google_deepmind_blog": "Google DeepMind Blog",
    "marktechpost": "MarkTechPost",
    "kakao_tech": "카카오 테크",
    "naver_d2": "네이버 D2",
    "toss_tech": "토스 테크",
    "woowahan_tech": "우아한형제들",
}
METRIC_LABELS = {
    "likes": "좋아요", "comments": "댓글", "shares": "공유", "views": "조회",
    "stars": "스타", "forks": "포크", "downloads": "다운로드",
}
MAX_BLOCKS_PER_MESSAGE = 50
MAX_TEXT_PER_MESSAGE = 34_000
SECTION_TEXT_LIMIT = 2_900


def _area_label(content: Content, summary: TopicSummary) -> str:
    details = [category.split("/", 1)[1] for category in content.categories[:2]]
    areas = [area for area in summary.areas if area in {"기획", "개발", "디자인"}]
    return " · ".join(dict.fromkeys(areas + details)) or "기술"


def _metric_text(content: Content) -> str:
    source = SOURCE_LABELS.get(content.source, content.source)
    if content.source == "github":
        return (
            f"{source} · ⭐ {content.metrics.stars:,} · "
            f"Fork {content.metrics.forks:,}"
        )
    if content.source == "reddit":
        rank = content.raw_metadata.get("daily_rank")
        rank_text = f" #{rank}" if isinstance(rank, int) and rank > 0 else ""
        return f"{source} · 🔥 최근 24시간 인기{rank_text}"
    metrics = " · ".join(
        f"{METRIC_LABELS.get(key, key)} {value:,}"
        for key, value in content.metrics.model_dump().items()
        if value
    )
    return f"{source} · {metrics or '공개 지표 없음'}"


def _split_text(value: str, limit: int = SECTION_TEXT_LIMIT) -> list[str]:
    parts: list[str] = []
    remaining = value.strip()
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def _text_length(value: Any) -> int:
    if isinstance(value, dict):
        return sum(
            len(item) if key == "text" and isinstance(item, str) else _text_length(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(_text_length(item) for item in value)
    return 0


def _item_blocks(
    index: int, content: Content, summary: TopicSummary
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*{index}. [{_area_label(content, summary)}] "
                f"<{content.url}|{summary.title_ko}>*"
            ),
        },
    }]
    blocks.extend(
        {"type": "section", "text": {"type": "mrkdwn", "text": part}}
        for part in _split_text(summary.summary_ko)
    )
    for part_index, part in enumerate(_split_text(summary.why_important, 2_850)):
        prefix = "*💡 실무 포인트*\n" if part_index == 0 else ""
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{prefix}{part}"},
        })
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": _metric_text(content)}],
    })
    blocks.append({"type": "divider"})
    return blocks


def _collection_blocks(
    collection_results: list[CollectionResult] | None,
) -> list[dict[str, Any]]:
    if collection_results is None:
        return []
    status_text = "수집 상태: " + " · ".join(
        f"{SOURCE_LABELS.get(result.source, result.source)} "
        f"{_collection_status_label(result)}"
        for result in collection_results
    )
    blocks = [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": part}],
        }
        for part in _split_text(status_text)
    ]
    warnings = [
        f"{SOURCE_LABELS.get(result.source, result.source)} {_warning_label(result)}"
        for result in collection_results
        if result.status in {
            CollectionStatus.PARTIAL,
            CollectionStatus.FAILED,
            CollectionStatus.NOT_CONFIGURED,
        }
    ]
    if warnings:
        warning_text = "주의: " + ", ".join(warnings)
        blocks.extend({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": part}],
        } for part in _split_text(warning_text))
    return blocks


def _report_shell(
    rows: list[tuple[Content, TopicSummary]],
    start: datetime,
    end: datetime,
    collection_results: list[CollectionResult] | None,
    group_name: str,
    page: int,
    page_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    area_counts = {area: 0 for area in ("기획", "개발", "디자인")}
    source_counts: dict[str, int] = {}
    for content, summary in rows:
        for area in set(summary.areas):
            if area in area_counts:
                area_counts[area] += 1
        source_counts[content.source] = source_counts.get(content.source, 0) + 1
    composition = " · ".join(
        f"{area} {count}" for area, count in area_counts.items() if count
    )
    sources = " · ".join(
        f"{SOURCE_LABELS.get(source, source)} {count}"
        for source, count in source_counts.items()
    )
    page_label = f" · {page}/{page_count}" if page_count > 1 else ""
    prefix: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🤖 {group_name} 브리프{page_label}"},
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"{end:%Y-%m-%d} · 총 {len(rows)}개 소식",
            }],
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f"분야: {composition or '분류 없음'}  |  "
                    f"출처: {sources or '없음'}"
                ),
            }],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🔥 오늘의 주목할 소식*"},
        },
    ]
    if not rows:
        prefix.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "이 기간에 수집된 소식이 없습니다."},
        })
    suffix = _collection_blocks(collection_results)
    suffix.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                f"집계: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} · "
                f"생성: {end:%H:%M %Z}"
            ),
        }],
    })
    return prefix, suffix


def _fits_message(
    content_blocks: list[dict[str, Any]],
    shell_blocks: list[dict[str, Any]],
) -> bool:
    blocks = shell_blocks + content_blocks
    return (
        len(blocks) <= MAX_BLOCKS_PER_MESSAGE
        and sum(_text_length(block) for block in blocks) <= MAX_TEXT_PER_MESSAGE
    )


def build_messages(
    rows: list[tuple[Content, TopicSummary]],
    start: datetime,
    end: datetime,
    collection_results: list[CollectionResult] | None = None,
    news_sources: set[str] | None = None,
) -> dict[str, list[list[dict[str, Any]]]]:
    news_sources = news_sources or set()
    groups: dict[str, list[tuple[Content, TopicSummary]]] = {
        "AI 뉴스": [],
        "AI 기술": [],
    }
    for row in rows:
        group_name = "AI 뉴스" if row[0].source in news_sources else "AI 기술"
        groups[group_name].append(row)

    messages: dict[str, list[list[dict[str, Any]]]] = {}
    for group_name, group_rows in groups.items():
        group_results = (
            [
                result for result in collection_results
                if ("AI 뉴스" if result.source in news_sources else "AI 기술") == group_name
            ]
            if collection_results is not None else None
        )
        prefix, suffix = _report_shell(
            group_rows, start, end, group_results, group_name, 1, 1
        )
        shell_blocks = prefix + suffix
        item_blocks = [
            _item_blocks(index, content, summary)
            for index, (content, summary) in enumerate(group_rows, start=1)
        ]
        pages: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for entry_blocks in item_blocks:
            candidate = current + entry_blocks
            if current and not _fits_message(candidate, shell_blocks):
                pages.append(current)
                current = []
                candidate = entry_blocks
            if _fits_message(candidate, shell_blocks):
                current = candidate
                continue
            for block in entry_blocks:
                if current and not _fits_message(current + [block], shell_blocks):
                    pages.append(current)
                    current = []
                current.append(block)
        if current or not pages:
            pages.append(current)

        group_messages = []
        for page_number, page_blocks in enumerate(pages, start=1):
            prefix, suffix = _report_shell(
                group_rows,
                start,
                end,
                group_results,
                group_name,
                page_number,
                len(pages),
            )
            group_messages.append(prefix + page_blocks + suffix)
        messages[group_name] = group_messages
    return messages


def build_blocks(
    rows: list[tuple[Content, TopicSummary]],
    start: datetime,
    end: datetime,
    collection_results: list[CollectionResult] | None = None,
) -> list[dict[str, Any]]:
    messages = build_messages(rows, start, end, collection_results)
    return [block for page in messages["AI 기술"] for block in page]


def _status_label(status: CollectionStatus) -> str:
    return {
        CollectionStatus.EMPTY: "검색 결과 없음",
        CollectionStatus.PARTIAL: "일부 실패",
        CollectionStatus.FAILED: "실패",
        CollectionStatus.DISABLED: "비활성화",
        CollectionStatus.NOT_CONFIGURED: "설정 필요",
        CollectionStatus.SUCCESS: "성공",
    }[status]


def _collection_status_label(result: CollectionResult) -> str:
    if result.status in {CollectionStatus.SUCCESS, CollectionStatus.PARTIAL}:
        return f"{result.accepted_count}건"
    return _status_label(result.status)


def _warning_label(result: CollectionResult) -> str:
    cause = {
        "authentication": "인증 실패",
        "permission": "권한 오류",
        "rate_limited": "호출 한도 초과",
        "invalid_query": "검색 요청 오류",
        "invalid_response": "응답 형식 오류",
        "transport": "네트워크 오류",
        "not_configured": "설정 필요",
    }.get(result.error_code or "")
    return cause or _status_label(result.status)


class SlackReporter:
    def __init__(self, store: Store, client: httpx.AsyncClient, webhook: str | None) -> None:
        self.store, self.client, self.webhook = store, client, webhook

    async def send(
        self,
        report_date: date,
        messages: dict[str, list[list[dict[str, Any]]]] | list[dict[str, Any]],
        force: bool = False,
        dry_run: bool = False,
    ) -> bool:
        from sqlalchemy import select
        if dry_run:
            return False
        if not self.webhook:
            raise RuntimeError("SLACK_WEBHOOK_URL is not configured")
        with self.store.session() as session:
            sent = session.scalar(select(ReportRow).where(ReportRow.report_date == report_date.isoformat()))
            if sent and not force:
                return False
        if isinstance(messages, list):
            messages = {"AI 기술": [messages]}
        message_index = 0
        for group_messages in messages.values():
            for blocks in group_messages:
                if message_index:
                    await asyncio.sleep(1)
                for attempt in range(3):
                    response = await self.client.post(self.webhook, json={"blocks": blocks})
                    if response.status_code != 429:
                        response.raise_for_status()
                        break
                    if attempt == 2:
                        response.raise_for_status()
                    try:
                        retry_after = float(response.headers.get("Retry-After", "1"))
                    except ValueError:
                        retry_after = 1
                    await asyncio.sleep(max(retry_after, 1))
                message_index += 1
        with self.store.session() as session:
            row = session.scalar(
                select(ReportRow).where(ReportRow.report_date == report_date.isoformat())
            )
            if row:
                row.sent_at = datetime.now().astimezone()
            else:
                session.add(ReportRow(
                    report_date=report_date.isoformat(),
                    sent_at=datetime.now().astimezone(),
                ))
        return True
