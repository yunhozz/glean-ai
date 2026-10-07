from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, and_, create_engine, delete, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .models import CollectionResult, Content


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


class GitHubStarSnapshotRow(Base):
    __tablename__ = "github_star_snapshots"
    __table_args__ = (
        UniqueConstraint("external_id", "observation_id", name="uq_github_star_observation"),
        Index("ix_github_star_external_observed", "external_id", "observed_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255))
    observation_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stars: Mapped[int] = mapped_column()


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
                    if content.source in {"github", "huggingface", "hacker_news_ai", "reddit"}:
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

    def latest_for_source(self, source: str, limit: int = 2) -> list[ContentRow]:
        with self.session() as session:
            return list(session.scalars(select(ContentRow).where(
                ContentRow.source == source
            ).order_by(
                ContentRow.published_at.desc(), ContentRow.external_id.asc()
            ).limit(max(0, limit))).all())


    def latest_github_run(self) -> RunRow | None:
        with self.session() as session:
            row = session.scalar(select(RunRow).where(
                RunRow.kind == "collect", RunRow.source == "github"
            ).order_by(RunRow.started_at.desc(), RunRow.id.desc()).limit(1))
        if row and row.started_at.tzinfo is None:
            row.started_at = row.started_at.replace(tzinfo=timezone.utc)
        return row

    def github_observations(
        self, external_ids: list[str], since: datetime, until: datetime
    ) -> list[GitHubStarSnapshotRow]:
        with self.session() as session:
            rows = list(session.scalars(select(GitHubStarSnapshotRow).where(
                GitHubStarSnapshotRow.external_id.in_(external_ids),
                GitHubStarSnapshotRow.observed_at >= since,
                GitHubStarSnapshotRow.observed_at <= until,
            )))
        for row in rows:
            if row.observed_at.tzinfo is None:
                row.observed_at = row.observed_at.replace(tzinfo=timezone.utc)
        return rows


    def persist_github_collection(
        self, contents: list[Content], result: CollectionResult, started_at: datetime
    ) -> int:
        with self.session() as session:
            run = RunRow(
                kind="collect", source="github", status=result.status,
                started_at=started_at, finished_at=datetime.now(timezone.utc),
                error=result.error_message, error_code=result.error_code,
                fetched_count=result.fetched_count, accepted_count=result.accepted_count,
            )
            session.add(run)
            session.flush()
            candidates = {c.external_id: c for c in contents if c.source == "github"}
            existing = {row.external_id: row for row in session.scalars(select(ContentRow).where(
                ContentRow.source == "github", ContentRow.external_id.in_(candidates)
            ))}
            inserted = 0
            if result.status in {"success", "partial"}:
                for content in candidates.values():
                    data = content.model_dump()
                    data["url"] = str(content.url)
                    row = existing.get(content.external_id)
                    if row is None:
                        session.add(ContentRow(**data))
                        inserted += 1
                    else:
                        for name, value in data.items():
                            setattr(row, name, value)
                    # Only explicitly supplied, valid counts are observations.
                    if "stars" in content.metrics.model_fields_set and content.metrics.stars >= 0:
                        session.add(GitHubStarSnapshotRow(
                            external_id=content.external_id, observation_id=run.id,
                            observed_at=started_at, stars=content.metrics.stars,
                        ))
            session.execute(delete(GitHubStarSnapshotRow).where(
                GitHubStarSnapshotRow.observed_at < started_at-timedelta(days=7)
            ))
        return inserted

    def github_report_rows(
        self, report_at: datetime
    ) -> list[tuple[ContentRow, GitHubStarSnapshotRow]]:
        latest = self.latest_github_run()
        if (latest is None or latest.status not in {'success', 'partial'}
                or not report_at-timedelta(hours=24) <= latest.started_at <= report_at):
            return []
        with self.session() as session:
            rows = list(session.execute(select(ContentRow, GitHubStarSnapshotRow).join(
                GitHubStarSnapshotRow,
                and_(ContentRow.source == 'github', ContentRow.external_id == GitHubStarSnapshotRow.external_id),
            ).where(GitHubStarSnapshotRow.observation_id == latest.id)))
        for _, snapshot in rows:
            if snapshot.observed_at.tzinfo is None:
                snapshot.observed_at = snapshot.observed_at.replace(tzinfo=timezone.utc)
        return [(content, snapshot) for content, snapshot in rows]
