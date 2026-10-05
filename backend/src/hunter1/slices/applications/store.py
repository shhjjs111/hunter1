"""applications 切片的持久化门面 —— 本切片**唯一**接触数据库的地方。

好处（不只是形式）：
1. 「数据从哪来」有唯一答案 —— Agent 读切片时不必翻遍 repository；
2. 未来换实现（加缓存层、换存储）只动这里，service/router 不动。

当前薄委托 `platform.db` 的 `SqliteApplicationRepository`；表定义（ApplicationRow）
与仓储实现的归位（搬进本切片）在后续波次完成（见 SLICE.md 的迁移注）。
"""

from __future__ import annotations

from hunter1.domain.models import Application
from hunter1.platform.db import Database
from hunter1.platform.db.applications import SqliteApplicationRepository


class ApplicationStore:
    """投递记录的数据存取。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, application: Application) -> None:
        """存在即更新，否则插入。"""
        self._db.applications().upsert(application)

    def get(self, application_id: str) -> Application | None:
        return self._db.applications().get(application_id)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Application]:
        """一页投递记录（按 `updated_at` 倒序）。"""
        repository: SqliteApplicationRepository = self._db.applications()
        return repository.list(limit=limit, offset=offset)

    def by_job(self, job_id: str) -> list[Application]:
        return self._db.applications().by_job(job_id)

    def count(self) -> int:
        return self._db.applications().count()

    def delete(self, application_id: str) -> None:
        self._db.applications().delete(application_id)


__all__ = ["ApplicationStore"]
