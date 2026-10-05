"""仓储实现 —— SQLite 版，实现 application 层定义的端口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from hunter1.domain.models import CaptureStatus, Company, Job
from hunter1.infrastructure.db.schema import CompanyRow, JobRow

if TYPE_CHECKING:
    from hunter1.infrastructure.db.database import Database


def _to_company(row: CompanyRow) -> Company:
    return Company(
        id=row.id,
        name=row.name,
        source=row.source,
        source_ref=row.source_ref,
        aliases=list(row.aliases or []),
        campus_url=row.campus_url,
        crawler_key=row.crawler_key,
    )


def _to_job(row: JobRow) -> Job:
    return Job(
        id=row.id,
        company_id=row.company_id,
        title=row.title,
        detail_url=row.detail_url,
        source=row.source,
        source_ref=row.source_ref,
        city=row.city,
        jd_raw=row.jd_raw,
        match_score=row.match_score,
        capture_status=CaptureStatus(row.capture_status),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
    )


class SqliteCompanyRepository:
    """公司仓储：upsert 语义（存在即更新，否则插入）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def upsert(self, company: Company) -> None:
        with self._db.session() as session:
            row = session.get(CompanyRow, company.id)
            if row is None:
                row = CompanyRow(id=company.id)
                session.add(row)
            row.name = company.name
            row.source = company.source
            row.source_ref = company.source_ref
            row.aliases = list(company.aliases)
            row.campus_url = company.campus_url
            row.crawler_key = company.crawler_key
            session.commit()

    def get(self, company_id: str) -> Company | None:
        with self._db.session() as session:
            row = session.get(CompanyRow, company_id)
            return _to_company(row) if row is not None else None

    def count(self) -> int:
        with self._db.session() as session:
            total = session.scalar(select(func.count()).select_from(CompanyRow))
            return int(total or 0)


class SqliteJobRepository:
    """岗位仓储。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def upsert(self, job: Job) -> None:
        with self._db.session() as session:
            row = session.get(JobRow, job.id)
            if row is None:
                row = JobRow(id=job.id)
                session.add(row)
            row.company_id = job.company_id
            row.title = job.title
            row.title_key = job.title_key
            row.detail_url = job.detail_url
            row.source = job.source
            row.source_ref = job.source_ref
            row.city = job.city
            row.jd_raw = job.jd_raw
            row.match_score = job.match_score
            row.capture_status = job.capture_status.value
            row.first_seen_at = job.first_seen_at
            row.last_seen_at = job.last_seen_at
            session.commit()

    def get(self, job_id: str) -> Job | None:
        with self._db.session() as session:
            row = session.get(JobRow, job_id)
            return _to_job(row) if row is not None else None

    def count(self, *, company_id: str | None = None) -> int:
        with self._db.session() as session:
            statement = select(func.count()).select_from(JobRow)
            if company_id is not None:
                statement = statement.where(JobRow.company_id == company_id)
            total = session.scalar(statement)
            return int(total or 0)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Job]:
        with self._db.session() as session:
            statement = (
                select(JobRow).order_by(JobRow.last_seen_at.desc()).limit(limit).offset(offset)
            )
            return [_to_job(row) for row in session.scalars(statement)]


__all__ = ["SqliteCompanyRepository", "SqliteJobRepository"]
