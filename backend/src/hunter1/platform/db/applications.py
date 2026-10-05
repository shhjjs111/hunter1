"""投递记录仓储 —— SQLite 版，实现 application 层定义的端口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

from hunter1.domain.models import Application, ApplicationStage
from hunter1.platform.db.schema import ApplicationRow

if TYPE_CHECKING:
    from hunter1.platform.db.database import Database


def _to_application(row: ApplicationRow) -> Application:
    return Application(
        id=row.id,
        job_id=row.job_id,
        company=row.company,
        title=row.title,
        stage=ApplicationStage(row.stage),
        applied_at=row.applied_at,
        updated_at=row.updated_at,
        note=row.note,
    )


class SqliteApplicationRepository:
    """投递记录仓储：upsert 语义（存在即更新，否则插入）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def upsert(self, application: Application) -> None:
        with self._db.session() as session:
            row = session.get(ApplicationRow, application.id)
            if row is None:
                row = ApplicationRow(id=application.id)
                session.add(row)
            row.job_id = application.job_id
            row.company = application.company
            row.title = application.title
            row.stage = application.stage.value
            row.applied_at = application.applied_at
            row.updated_at = application.updated_at
            row.note = application.note
            session.commit()

    def get(self, application_id: str) -> Application | None:
        with self._db.session() as session:
            row = session.get(ApplicationRow, application_id)
            return _to_application(row) if row is not None else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Application]:
        with self._db.session() as session:
            statement = (
                select(ApplicationRow)
                .order_by(ApplicationRow.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [_to_application(row) for row in session.scalars(statement)]

    def by_job(self, job_id: str) -> list[Application]:
        with self._db.session() as session:
            statement = (
                select(ApplicationRow)
                .where(ApplicationRow.job_id == job_id)
                .order_by(ApplicationRow.updated_at.desc())
            )
            return [_to_application(row) for row in session.scalars(statement)]

    def count(self) -> int:
        with self._db.session() as session:
            total = session.scalar(select(func.count()).select_from(ApplicationRow))
            return int(total or 0)

    def delete(self, application_id: str) -> None:
        with self._db.session() as session:
            session.execute(delete(ApplicationRow).where(ApplicationRow.id == application_id))
            session.commit()


__all__ = ["SqliteApplicationRepository"]
