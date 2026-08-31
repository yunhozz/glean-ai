from datetime import date, datetime
from typing import Any

import httpx

from .models import Content, TopicSummary
from .storage import ReportRow, Store


def build_blocks(rows: list[tuple[Content, TopicSummary]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "오늘의 AI Product · Dev · Design Brief"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"집계: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}"}]},
    ]
    for rank, (content, summary) in enumerate(rows, 1):
        metrics = ", ".join(f"{k} {v}" for k, v in content.metrics.model_dump().items() if v) or "지표 없음"
        text = f"*{rank}. <{content.url}|{summary.title_ko}>* · {content.final_score:.1f}점\n{summary.summary_ko}\n_{content.source} · {metrics}_"
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
