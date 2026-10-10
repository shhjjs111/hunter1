"""applications 切片的持久化门面 —— 本切片**唯一**接触数据库的地方。

好处（不只是形式）：
1. 「数据从哪来」有唯一答案 —— Agent 读切片时不必翻遍 repository；
2. 未来换实现（加缓存层、换存储）只动这里，service/router 不动。

当前薄委托 `platform.db` 的 `SqliteApplicationRepository`；表定义（ApplicationRow）
与仓储实现的归位（搬进本切片）**仍未完成** —— 这是有意的过渡期技术债，
见 `docs/ARCHITECTURE.md` 的「已知取舍与技术债」。
"""

from __future__ import annotations

from datetime import datetime

from hunter1.domain.models import Application
from hunter1.platform.db import Database
from hunter1.platform.db.applications import SqliteApplicationRepository


class ApplicationStore:
    """投递记录的数据存取。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, application: Application) -> None:
        """存在即更新，否则插入（按**主键**）。"""
        self._db.applications().upsert(application)

    def insert_for_job(self, application: Application) -> Application:
        """插入一条投递；该岗位已有记录时返回既有的那条（并发下也是幂等的）。

        竞态兜底在数据库（`UNIQUE(job_id)`）—— 见 `platform/db/applications.py`
        的 `insert_for_job`。`upsert` 按主键判断，挡不住「两个请求各生成新 uuid」。
        """
        return self._db.applications().insert_for_job(application)

    def update_existing(
        self, application: Application, *, expected_updated_at: datetime | None = None
    ) -> bool:
        """只更新已存在的行；行已被删返回 False（**不**把它插回去）。

        `expected_updated_at` 见平台仓储的同名参数：不传就是不做版本校验，
        传了就是**乐观锁** —— 拿下并发下的读-改-写丢更新（阶段推进就是这种写法）。
        """
        return self._db.applications().update_existing(
            application, expected_updated_at=expected_updated_at
        )

    def get(self, application_id: str) -> Application | None:
        return self._db.applications().get(application_id)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Application]:
        """一页投递记录（按 `updated_at` 倒序）。"""
        repository: SqliteApplicationRepository = self._db.applications()
        return repository.list(limit=limit, offset=offset)

    def by_job(self, job_id: str) -> list[Application]:
        return self._db.applications().by_job(job_id)

    def by_job_prefix(self, prefix: str, *, limit: int = 20) -> list[Application]:
        """按岗位 id 前缀查投递（助手看到的是 8 位前缀）。"""
        return self._db.applications().by_job_prefix(prefix, limit=limit)

    def count(self) -> int:
        return self._db.applications().count()

    def delete(self, application_id: str) -> None:
        self._db.applications().delete(application_id)


__all__ = ["ApplicationStore"]
