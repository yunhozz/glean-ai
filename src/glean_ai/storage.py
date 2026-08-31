from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, Text, UniqueConstraint, create_engine, select
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


class ReportRow(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[str] = mapped_column(String(10), unique=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Store:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(database_url)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            yield session
            session.commit()

    def upsert(self, content: Content) -> bool:
        with self.session() as session:
            existing = session.scalar(select(ContentRow).where(
                ContentRow.source == content.source,
                ContentRow.external_id == content.external_id,
            ))
            if existing:
                return False
            data = content.model_dump()
            data["url"] = str(content.url)
            data["metrics"] = content.metrics.model_dump()
            session.add(ContentRow(**data))
            return True

    def recent(self, since: datetime) -> list[ContentRow]:
        with self.session() as session:
            return list(session.scalars(select(ContentRow).where(
                ContentRow.published_at >= since
            ).order_by(ContentRow.final_score.desc())).all())
