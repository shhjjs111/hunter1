"""jobs 切片的持久化门面 —— 本切片**唯一**接触数据库的地方。

好处（不只是形式）：
1. 「数据从哪来」有唯一答案 —— Agent 读切片时不必翻遍 repository；
2. 未来换实现（加缓存层、换存储）只动这里，service/router 不动。

当前委托 `platform.db` 的 SQLite 仓储；表定义（JobRow / CompanyRow）与仓储
实现的归位（搬进本切片）**仍未完成** —— 这是有意的过渡期技术债，
见 `docs/ARCHITECTURE.md` 的「已知取舍与技术债」。
"""

from __future__ import annotations

from hunter1.domain.models import Job
from hunter1.platform.db import Database
from hunter1.platform.db.repository import SqliteJobRepository


class JobStore:
    """岗位 / 公司 / 投递入口的数据存取。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- 岗位 ----

    def get(self, job_id: str) -> Job | None:
        return self._db.jobs().get(job_id)

    def get_by_prefix(self, prefix: str, *, limit: int = 20) -> list[Job]:
        return self._db.jobs().get_by_prefix(prefix, limit=limit)

    def page(self, *, keyword: str, limit: int, offset: int) -> list[Job]:
        """一页岗位（`keyword` 为空 = 列最近）。"""
        repository: SqliteJobRepository = self._db.jobs()
        if keyword:
            return repository.search(keyword=keyword, limit=limit, offset=offset)
        return repository.list(limit=limit, offset=offset)

    def count(self, *, keyword: str) -> int:
        repository: SqliteJobRepository = self._db.jobs()
        return repository.count(keyword=keyword) if keyword else repository.count()


__all__ = ["JobStore"]
