"""scoring 切片的持久化门面 —— 本切片唯一接触数据库的地方。

读写都走平台仓储；写回时**走一次 model_validate**（不是 model_copy）：
`match_score` 带 ge=0/le=100 约束，model_copy 不重跑校验器，越界值会被
静默写库（与 crawl 切片合并不变量是同一个教训）。
"""

from __future__ import annotations

from hunter1.domain.models import Job
from hunter1.platform.db import Database


class ScoreStore:
    """岗位评分的读写。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def load(self, job_id: str) -> Job | None:
        return self._db.jobs().get(job_id)

    def save_score(self, job_id: str, score: int) -> Job | None:
        """把分数写回岗位；岗位不存在时返回 None（调用方据此报 404）。"""
        job = self._db.jobs().get(job_id)
        if job is None:
            return None
        payload = job.model_dump(exclude_computed_fields=True)
        payload["match_score"] = score
        updated = Job.model_validate(payload)
        self._db.jobs().upsert(updated)
        return updated


__all__ = ["ScoreStore"]
