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


def _area_label(content: Content, summary: TopicSummary) -> str:
    details = [category.split("/", 1)[1] for category in content.categories[:2]]
    areas = [area for area in summary.areas if area in {"기획", "개발", "디자인"}]
    return " · ".join(dict.fromkeys(areas + details)) or "AI"


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
        {"type": "header", "text": {"type": "plain_text", "text": "오늘의 AI Product · Dev · Design Brief"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"집계: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}"}]},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"구성: {composition or '분류 없음'}  |  출처: {sources or '없음'}"}]},
    ]
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
    for rank, (content, summary) in enumerate(rows, 1):
        metrics = ", ".join(
            f"{METRIC_LABELS.get(key, key)} {value:,}"
            for key, value in content.metrics.model_dump().items() if value
        ) or "공개 지표 없음"
        source = SOURCE_LABELS.get(content.source, content.source)
        text = (
            f"*{rank}. [{_area_label(content, summary)}] <{content.url}|{summary.title_ko}>*\n"
            f"{summary.summary_ko}\n*실무 포인트:* {summary.why_important}\n"
            f"_{source} · {metrics}_"
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"생성: {end:%Y-%m-%d %H:%M %Z}"}]})
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
