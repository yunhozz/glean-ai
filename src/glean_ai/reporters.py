from datetime import date, datetime
from typing import Any

import httpx

from .models import Content, TopicSummary
from .storage import ReportRow, Store

SOURCE_LABELS = {"github": "GitHub", "huggingface": "Hugging Face", "reddit": "Reddit"}
METRIC_LABELS = {
    "likes": "좋아요", "comments": "댓글", "shares": "공유", "views": "조회",
    "stars": "스타", "forks": "포크", "downloads": "다운로드",
}


def _area_label(content: Content, summary: TopicSummary) -> str:
    details = [category.split("/", 1)[1] for category in content.categories[:2]]
    areas = [area for area in summary.areas if area in {"기획", "개발", "디자인"}]
    return " · ".join(dict.fromkeys(areas + details)) or "AI"


def build_blocks(rows: list[tuple[Content, TopicSummary]], start: datetime, end: datetime) -> list[dict[str, Any]]:
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
    for rank, (content, summary) in enumerate(rows, 1):
        metrics = ", ".join(
            f"{METRIC_LABELS.get(key, key)} {value:,}"
            for key, value in content.metrics.model_dump().items() if value
        ) or "공개 지표 없음"
        source = SOURCE_LABELS.get(content.source, content.source)
        text = (
            f"*{rank}. [{_area_label(content, summary)}] <{content.url}|{summary.title_ko}>*\n"
            f"{summary.summary_ko}\n*왜 중요한가:* {summary.why_important}\n"
            f"_{source} · {metrics}_"
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"생성: {end:%Y-%m-%d %H:%M %Z}"}]})
    return blocks


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
