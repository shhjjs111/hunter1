"""投递记录仓储 —— SQLite 版，实现 application 层定义的端口。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from hunter1.domain.models import Application, ApplicationStage
from hunter1.platform.db.enums import restore_enum
from hunter1.platform.db.like import escape_like
from hunter1.platform.db.pagination import check_page
from hunter1.platform.db.schema import ApplicationRow

if TYPE_CHECKING:
    from hunter1.platform.db.database import Database


def _to_application(row: ApplicationRow) -> Application:
    return Application(
        id=row.id,
        job_id=row.job_id,
        company=row.company,
        title=row.title,
        stage=restore_enum(
            ApplicationStage, row.stage, default=ApplicationStage.APPLIED, where="投递阶段"
        ),
        applied_at=row.applied_at,
        updated_at=row.updated_at,
        note=row.note,
    )


def _copy_into(row: ApplicationRow, application: Application) -> None:
    """把领域对象写进行（插入与更新共用，避免两处字段列表漂移）。"""
    row.job_id = application.job_id
    row.company = application.company
    row.title = application.title
    row.stage = application.stage.value
    row.applied_at = application.applied_at
    row.updated_at = application.updated_at
    row.note = application.note


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
            _copy_into(row, application)
            session.commit()

    def insert_for_job(self, application: Application) -> Application:
        """插入一条投递；**该岗位已有记录时返回既有的那条**。

        「重复点击不该堆出多条投递」这条不变量由 `applications/service.py` 的
        「先读后写」表达，但那段判断在并发下会失效：两个请求都读到空、各插一条。
        真正的兜底必须在数据库 —— `uq_applications_job`（UNIQUE(job_id)）拒掉第二个
        INSERT，这里再读一次把既有的那条交出去（对调用方就是「幂等」）。

        之所以不靠 `upsert`：upsert 按**主键**判断存在性，而两个并发请求各自生成了
        新的 uuid，主键根本不会撞 —— 撞的是 job_id。
        """
        with self._db.session() as session:
            try:
                row = ApplicationRow(id=application.id)
                _copy_into(row, application)
                session.add(row)
                session.commit()
                return application
            except IntegrityError as exc:
                session.rollback()
                existing = session.scalars(
                    select(ApplicationRow)
                    .where(ApplicationRow.job_id == application.job_id)
                    .order_by(ApplicationRow.updated_at.desc(), ApplicationRow.id.asc())
                ).first()
                if existing is None:
                    # 撞的不是「同岗位唯一」这条约束 —— 原样抛，别把别的问题藏起来
                    raise exc
                return _to_application(existing)

    def update_existing(
        self, application: Application, *, expected_updated_at: datetime | None = None
    ) -> bool:
        """只更新**已存在**的行；行不在（已被删）返回 `False`，**不插入**。

        原先的阶段推进是 `get` 之后再 `upsert`：两步之间记录被删除时，upsert 会把
        它当成新记录插回去 —— 用户明明删了，刷新又回来了（静默撤销删除）。

        `expected_updated_at` 是**乐观锁**：调用方给出它**读到**的那一版的
        `updated_at`，写入时要求行仍是那一版。否则两个并发请求各读一次、各写一次，
        后写的那个会把先写的整体覆盖掉（读-改-写丢更新）—— 而两边的响应都是 200，
        没人会知道有一边的改动消失了。

        传 `None` = 不做版本校验（只在调用方明确要覆盖时才用）。
        """
        with self._db.session() as session:
            statement = update(ApplicationRow).where(ApplicationRow.id == application.id)
            if expected_updated_at is not None:
                # 与读取时同一格式（`UtcDateTime` 的 bind/result 是对称的），
                # 所以这里是精确比较，不需要容差。
                statement = statement.where(ApplicationRow.updated_at == expected_updated_at)
            outcome = session.execute(
                statement.values(
                    job_id=application.job_id,
                    company=application.company,
                    title=application.title,
                    stage=application.stage.value,
                    applied_at=application.applied_at,
                    updated_at=application.updated_at,
                    note=application.note,
                )
            )
            session.commit()
            # `rowcount` 只在 CursorResult 上（DML 一定返回它），而 `Result` 的静态类型
            # 没有这个属性 —— 用 isinstance 收窄事实，别用 cast / type: ignore 糊过去。
            return isinstance(outcome, CursorResult) and outcome.rowcount > 0

    def get(self, application_id: str) -> Application | None:
        with self._db.session() as session:
            row = session.get(ApplicationRow, application_id)
            return _to_application(row) if row is not None else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Application]:
        # 第二键 id：同 updated_at 行的顺序 SQL 不作保证，而本方法直接走 offset 分页
        # （批量导入/脚本写入时同秒不罕见）。与 jobs 仓储同款，理由见 repository.py 的 _JOB_ORDER。
        check_page(limit=limit, offset=offset)
        with self._db.session() as session:
            statement = (
                select(ApplicationRow)
                .order_by(ApplicationRow.updated_at.desc(), ApplicationRow.id.asc())
                .limit(limit)
                .offset(offset)
            )
            return [_to_application(row) for row in session.scalars(statement)]

    def by_job(self, job_id: str) -> list[Application]:
        with self._db.session() as session:
            statement = (
                select(ApplicationRow)
                .where(ApplicationRow.job_id == job_id)
                .order_by(ApplicationRow.updated_at.desc(), ApplicationRow.id.asc())
            )
            return [_to_application(row) for row in session.scalars(statement)]

    def by_job_prefix(self, prefix: str, *, limit: int = 20) -> list[Application]:
        """按 `job_id` 前缀查投递 —— 助手看到的岗位 id 是 8 位前缀。

        与 `by_job`（精确）分开：精确查询是路由/界面用的，前缀查询是给「模型只记得
        前几位」的场景兜底。混成一个会让「按前缀查」的歧义判断渗进精确路径。

        `limit` 必须有（单字符前缀会命中很多行；这里只用来找唯一那条 / 判歧义）。
        """
        if not prefix:
            return []
        escaped = escape_like(prefix)
        with self._db.session() as session:
            statement = (
                select(ApplicationRow)
                .where(ApplicationRow.job_id.like(f"{escaped}%", escape="\\"))
                .order_by(ApplicationRow.updated_at.desc(), ApplicationRow.id.asc())
                .limit(limit)
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
