from datetime import date, datetime
from typing import Any

import httpx

from .models import CollectionResult, CollectionStatus, Content, TopicSummary
from .storage import ReportRow, Store

SOURCE_LABELS = {
    "github": "GitHub",
    "huggingface": "Hugging Face",
    "reddit": "Reddit",
    "threads": "Threads",
}
METRIC_LABELS = {
    "likes": "좋아요", "comments": "댓글", "shares": "공유", "views": "조회",
    "stars": "스타", "forks": "포크", "downloads": "다운로드",
}
DETAIL_LIMIT = 3
RANK_LABELS = ("①", "②", "③")


def _area_label(content: Content, summary: TopicSummary) -> str:
    details = [category.split("/", 1)[1] for category in content.categories[:2]]
    areas = [area for area in summary.areas if area in {"기획", "개발", "디자인"}]
    return " · ".join(dict.fromkeys(areas + details)) or "AI"


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


def build_blocks(
    rows: list[tuple[Content, TopicSummary]],
    start: datetime,
    end: datetime,
    collection_results: list[CollectionResult] | None = None,
) -> list[dict[str, Any]]:
    area_counts = {area: 0 for area in ("기획", "개발", "디자인")}
    source_counts: dict[str, int] = {}
    for content, summary in rows:
        for area in set(summary.areas):
            if area in area_counts:
                area_counts[area] += 1
        source_counts[content.source] = source_counts.get(content.source, 0) + 1
    composition = " · ".join(f"{area} {count}" for area, count in area_counts.items() if count)
    sources = " · ".join(
        f"{SOURCE_LABELS.get(source, source)} {count}" for source, count in source_counts.items()
    )
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🤖 오늘의 AI 브리프"},
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
    ]
    detailed = rows[:DETAIL_LIMIT]
    if detailed:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🔥 오늘의 주목할 소식*"},
        })
    for index, (content, summary) in enumerate(detailed):
        text = (
            f"*{RANK_LABELS[index]} [{_area_label(content, summary)}] "
            f"<{content.url}|{summary.title_ko}>*\n"
            f"{summary.summary_ko}\n\n"
            f"*💡 실무 포인트*\n{summary.why_important}"
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _metric_text(content)}],
        })
        blocks.append({"type": "divider"})

    compact = rows[DETAIL_LIMIT:]
    if compact:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📌 함께 볼 소식*"},
        })
        compact_text = "\n\n".join(
            f"• *<{content.url}|{summary.title_ko}>*\n"
            f"  _{_area_label(content, summary)} · {_metric_text(content)}_"
            for content, summary in compact
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": compact_text[:2900]},
        })

    if collection_results is not None:
        status_text = " · ".join(
            f"{SOURCE_LABELS.get(result.source, result.source)} "
            f"{_collection_status_label(result)}"
            for result in collection_results
        )
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"수집 상태: {status_text}"}],
        })
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
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"주의: {', '.join(warnings)}"}],
            })
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                f"집계: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} · "
                f"생성: {end:%H:%M %Z}"
            ),
        }],
    })
    return blocks


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

    async def send(self, report_date: date, blocks: list[dict[str, Any]], force: bool = False, dry_run: bool = False) -> bool:
        from sqlalchemy import select
        if dry_run:
            return False
        if not self.webhook:
            raise RuntimeError("SLACK_WEBHOOK_URL is not configured")
        with self.store.session() as session:
            sent = session.scalar(select(ReportRow).where(ReportRow.report_date == report_date.isoformat()))
            if sent and not force:
                return False
        response = await self.client.post(self.webhook, json={"blocks": blocks})
        response.raise_for_status()
        with self.store.session() as session:
            session.add(ReportRow(report_date=report_date.isoformat(), sent_at=datetime.now().astimezone()))
        return True
