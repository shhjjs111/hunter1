"""scoring 切片的持久化门面 —— 本切片唯一接触数据库的地方。

读写都走平台仓储；写回时**走一次 model_validate**（不是 model_copy）：
`match_score` 带 ge=0/le=100 约束，model_copy 不重跑校验器，越界值会被
静默写库（与 crawl 切片合并不变量是同一个教训）。
"""

from __future__ import annotations

from pydantic import ValidationError

from hunter1.domain.models import Job
from hunter1.platform.db import Database
from hunter1.slices.scoring.models import CandidateProfile

# 画像存在通用键值配置区（与 LLM 配置同表）：它是**用户配置**，不是岗位数据，
# 落在这里才能被界面改写后立刻生效，而不必改装配代码重启。
PROFILE_KEY = "candidate_profile"


class ScoreStore:
    """岗位评分与候选人画像的读写。"""

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

    # ---- 候选人画像 ----

    def load_profile(self) -> CandidateProfile | None:
        """读画像：**没配过返回 None，配过但坏了抛错**。

        两者必须能分辨（与 `platform.db` 的 LLM 配置同款取舍）：`None` 是初始
        状态，界面该渲染空表单；抛错是数据损坏，界面该给修复指引 —— 若把损坏
        也当成「还没配」静默返回 None，用户填过的内容会被无声丢弃。
        """
        raw = self._db.settings().get_raw(PROFILE_KEY)
        if raw is None:
            return None
        try:
            return CandidateProfile.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"已保存的候选人画像不合法：{exc}") from exc

    def save_profile(self, profile: CandidateProfile) -> None:
        self._db.settings().set_raw(PROFILE_KEY, profile.model_dump())


__all__ = ["PROFILE_KEY", "ScoreStore"]
