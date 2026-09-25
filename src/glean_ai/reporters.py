import asyncio
from datetime import date, datetime
from typing import Any

import httpx

from .models import CollectionResult, CollectionStatus, Content, TopicSummary
from .storage import ReportDeliveryRow, ReportRow, Store

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


def _platform_shell(
    rows: list[tuple[Content, TopicSummary]],
    start: datetime,
    end: datetime,
    collection_results: list[CollectionResult] | None,
    source_name: str,
    group_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prefix: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🤖 {source_name} · {group_name}",
            },
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"{end:%Y-%m-%d} · {len(rows)}개 소식",
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


def _truncate_text(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    if limit <= 0:
        return ""
    if limit == 1:
        return "…"
    cutoff = limit - 1
    word_boundary = value.rfind(" ", 0, cutoff + 1)
    if word_boundary >= limit * 2 // 3:
        cutoff = word_boundary
    return f"{value[:cutoff].rstrip()}…"


def _allocate_item_budgets(capacities: list[int], available: int) -> list[int]:
    budgets = [0] * len(capacities)
    remaining = min(available, sum(capacities))
    while remaining:
        active = [index for index, cap in enumerate(capacities) if budgets[index] < cap]
        if not active:
            break
        share = max(1, remaining // len(active))
        for index in active:
            amount = min(share, capacities[index] - budgets[index], remaining)
            budgets[index] += amount
            remaining -= amount
            if not remaining:
                break
    return budgets


def _split_item_budget(summary: str, why: str, budget: int) -> tuple[str, str]:
    full_length = len(summary) + len(why)
    if full_length <= budget:
        return summary, why
    if not full_length or budget <= 0:
        return "", ""
    summary_budget = min(len(summary), budget * len(summary) // full_length)
    why_budget = min(len(why), budget - summary_budget)
    remaining = budget - summary_budget - why_budget
    if remaining:
        extra = min(len(summary) - summary_budget, remaining)
        summary_budget += extra
        remaining -= extra
    if remaining:
        why_budget += min(len(why) - why_budget, remaining)
    return _truncate_text(summary, summary_budget), _truncate_text(why, why_budget)


def _entry_blocks(entries: list[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current = ""
    for entry in entries:
        parts = _split_text(entry) if len(entry) > SECTION_TEXT_LIMIT else [entry]
        for part in parts:
            candidate = f"{current}\n\n{part}" if current else part
            if current and len(candidate) > SECTION_TEXT_LIMIT:
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": current},
                })
                current = part
            else:
                current = candidate
    if current:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": current}})
    return blocks


def _platform_message(
    rows: list[tuple[Content, TopicSummary]],
    start: datetime,
    end: datetime,
    collection_results: list[CollectionResult] | None,
    source_name: str,
    group_name: str,
) -> list[dict[str, Any]]:
    prefix, suffix = _platform_shell(
        rows, start, end, collection_results, source_name, group_name
    )
    shell = prefix + suffix
    item_data = []
    for index, (content, summary) in enumerate(rows, start=1):
        linked_title = (
            f"*{index}. [{_area_label(content, summary)}] "
            f"<{content.url}|{summary.title_ko}>*"
        )
        title = linked_title if len(linked_title) <= SECTION_TEXT_LIMIT else (
            f"{index}. [{_area_label(content, summary)}] {summary.title_ko}\n"
            f"<{content.url}|원문 보기>"
        )
        metric = f"_{_metric_text(content)}_"
        item_data.append((title, summary.summary_ko, summary.why_important, metric))

    separators = max(0, len(item_data) - 1) * 2
    fixed_lengths = [
        len(title) + len("*💡 실무 포인트*") + len(metric) + 5
        for title, _, _, metric in item_data
    ]
    shell_length = sum(_text_length(block) for block in shell)
    available_body = (
        MAX_TEXT_PER_MESSAGE
        - shell_length
        - sum(fixed_lengths)
        - separators
    )
    capacities = [
        max(0, min(
            len(summary) + len(why),
            SECTION_TEXT_LIMIT - fixed_length,
        ))
        for (_, summary, why, _), fixed_length in zip(item_data, fixed_lengths, strict=True)
    ]
    budgets = _allocate_item_budgets(capacities, available_body)
    entries = []
    for (title, summary, why, metric), budget in zip(item_data, budgets, strict=True):
        short_summary, short_why = _split_item_budget(summary, why, budget)
        entries.append(
            f"{title}\n{short_summary}\n\n"
            f"*💡 실무 포인트*\n{short_why}\n{metric}"
        )
    blocks = prefix + _entry_blocks(entries) + suffix
    if (
        len(blocks) > MAX_BLOCKS_PER_MESSAGE
        or sum(_text_length(block) for block in blocks) > MAX_TEXT_PER_MESSAGE
    ):
        raise ValueError(f"{source_name} results exceed a single Slack message limit")
    return blocks


def build_messages(
    rows: list[tuple[Content, TopicSummary]],
    start: datetime,
    end: datetime,
    collection_results: list[CollectionResult] | None = None,
    platforms: list[tuple[str, str, str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if platforms is None:
        sources = dict.fromkeys(
            [content.source for content, _ in rows]
            + [result.source for result in collection_results or []]
        )
        platforms = [
            (source, SOURCE_LABELS.get(source, source), "AI 기술")
            for source in sources
        ]
    rows_by_source: dict[str, list[tuple[Content, TopicSummary]]] = {}
    for row in rows:
        rows_by_source.setdefault(row[0].source, []).append(row)
    results_by_source = {
        result.source: result for result in collection_results or []
    }

    messages: dict[str, list[dict[str, Any]]] = {}
    for source, source_name, group_name in platforms:
        result = results_by_source.get(source)
        messages[source] = _platform_message(
            rows_by_source.get(source, []),
            start,
            end,
            [result] if result else None,
            source_name,
            group_name,
        )
    return messages


def build_blocks(
    rows: list[tuple[Content, TopicSummary]],
    start: datetime,
    end: datetime,
    collection_results: list[CollectionResult] | None = None,
) -> list[dict[str, Any]]:
    return _platform_message(
        rows, start, end, collection_results, "전체 소식", "AI 기술"
    )


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
        messages: dict[str, list[dict[str, Any]]] | list[dict[str, Any]],
        force: bool = False,
        dry_run: bool = False,
    ) -> bool:
        from sqlalchemy import select
        if dry_run:
            return False
        if not self.webhook:
            raise RuntimeError("SLACK_WEBHOOK_URL is not configured")
        report_day = report_date.isoformat()
        with self.store.session() as session:
            sent = session.scalar(select(ReportRow).where(ReportRow.report_date == report_day))
            if sent and not force:
                return False
            delivery_times = {
                source: sent_at
                for source, sent_at in session.execute(
                    select(ReportDeliveryRow.source, ReportDeliveryRow.sent_at).where(
                        ReportDeliveryRow.report_date == report_day
                    )
                )
            }
            resending = sent is not None and any(
                sent_at > sent.sent_at for sent_at in delivery_times.values()
            )
            if sent and resending:
                delivered = {
                    source for source, sent_at in delivery_times.items()
                    if sent_at > sent.sent_at
                }
            else:
                delivered = set() if sent else set(delivery_times)
        if isinstance(messages, list):
            messages = {"전체 소식": messages}
        message_index = 0
        for source, blocks in messages.items():
            if source in delivered:
                continue
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
            with self.store.session() as session:
                row = session.scalar(select(ReportDeliveryRow).where(
                    ReportDeliveryRow.report_date == report_day,
                    ReportDeliveryRow.source == source,
                ))
                if row:
                    row.sent_at = datetime.now().astimezone()
                else:
                    session.add(ReportDeliveryRow(
                        report_date=report_day,
                        source=source,
                        sent_at=datetime.now().astimezone(),
                    ))
            message_index += 1
        with self.store.session() as session:
            row = session.scalar(
                select(ReportRow).where(ReportRow.report_date == report_day)
            )
            if row:
                row.sent_at = datetime.now().astimezone()
            else:
                session.add(ReportRow(
                    report_date=report_day,
                    sent_at=datetime.now().astimezone(),
                ))
        return True
