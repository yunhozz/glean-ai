from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, Text, UniqueConstraint, and_, create_engine, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .models import Content


class Base(DeclarativeBase):
    pass


class ContentRow(Base):
    __tablename__ = "contents"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(32))
    author: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(String(16))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)
    matched_keywords: Mapped[list[str]] = mapped_column(JSON)
    categories: Mapped[list[str]] = mapped_column(JSON)
    topic_id: Mapped[str | None] = mapped_column(String(64), index=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0)
    trend_score: Mapped[float] = mapped_column(Float, default=0)
    quality_score: Mapped[float] = mapped_column(Float, default=0)
    freshness_score: Mapped[float] = mapped_column(Float, default=0)
    final_score: Mapped[float] = mapped_column(Float, default=0, index=True)
    score_reasons: Mapped[dict[str, float]] = mapped_column(JSON)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    source: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    fetched_count: Mapped[int] = mapped_column(default=0)
    accepted_count: Mapped[int] = mapped_column(default=0)


class ReportRow(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[str] = mapped_column(String(10), unique=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportDeliveryRow(Base):
    __tablename__ = "report_deliveries"
    __table_args__ = (UniqueConstraint("report_date", "source", name="uq_report_delivery_source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Store:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(database_url)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    def upsert(self, content: Content) -> bool:
        return self.upsert_many([content]) > 0

    def upsert_many(self, contents: list[Content]) -> int:
        if not contents:
            return 0
        with self.session() as session:
            existing = {
                row.external_id: row
                for row in session.scalars(select(ContentRow).where(
                    ContentRow.source == contents[0].source,
                    ContentRow.external_id.in_(item.external_id for item in contents),
                ))
            }
            inserted = 0
            for content in contents:
                data = content.model_dump()
                data["url"] = str(content.url)
                data["metrics"] = content.metrics.model_dump()
                row = existing.get(content.external_id)
                if row:
                    if content.source == "huggingface":
                        for name, value in data.items():
                            setattr(row, name, value)
                    continue
                row = ContentRow(**data)
                session.add(row)
                existing[content.external_id] = row
                inserted += 1
        return inserted

    def recent(self, since: datetime, tech_blog_sources: set[str] | None = None) -> list[ContentRow]:
        tech_blog_sources = tech_blog_sources or set()
        with self.session() as session:
            return list(session.scalars(select(ContentRow).where(
                or_(
                    and_(ContentRow.source == "huggingface", ContentRow.collected_at >= since),
                    and_(ContentRow.source != "huggingface", ContentRow.published_at >= since),
                    and_(ContentRow.source.in_(tech_blog_sources), ContentRow.collected_at >= since),
                )
            ).order_by(ContentRow.final_score.desc())).all())
