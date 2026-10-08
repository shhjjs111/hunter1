"""scoring 切片的持久化门面 —— 本切片唯一接触数据库的地方。

写分走**定向列更新**（只写 match_score）：整行读-改-写会与抓取线程的整行写
互相覆盖 —— 抓取的旧快照会把刚打的分回滚掉（丢更新竞态，见 `save_score`）。
代价是绕过了 pydantic 的字段约束，故写库前在 `save_score` 里显式兜住取值域
（与 `Job.match_score` 的 ge=0/le=100 一致）—— 否则越界值会被静默写库。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from hunter1.domain.models import Job
from hunter1.platform.db import Database
from hunter1.slices.scoring.models import CandidateProfile

# 画像存在通用键值配置区（与 LLM 配置同表）：它是**用户配置**，不是岗位数据，
# 落在这里才能被界面改写后立刻生效，而不必改装配代码重启。
PROFILE_KEY = "candidate_profile"

#: match_score 的取值域，与 `Job.match_score` 的约束一致。
SCORE_MIN = 0
SCORE_MAX = 100


class ScoreStore:
    """岗位评分与候选人画像的读写。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def load(self, job_id: str) -> Job | None:
        return self._db.jobs().get(job_id)

    def save_score(
        self,
        job_id: str,
        score: int,
        *,
        model: str | None = None,
        prompt_version: str | None = None,
        scored_at: datetime | None = None,
    ) -> Job | None:
        """把分数写回岗位；岗位不存在时返回 None（调用方据此报 404）。

        **定向只写评分那几列**，不是整行读-改-写：否则抓取线程用它的旧快照
        整行回写时，会把这里刚打的分覆盖回 None（丢更新竞态）。代价是绕过 pydantic
        约束，故写库前显式校验取值域。

        `model` / `prompt_version` / `scored_at` 是**溯源**：`prompts.py` 说
        PROMPT_VERSION 就是为了「回溯这条分是哪版打出来的」，那就得真的落库
        （原先只在 HTTP 响应里回显，刷新页面即无据可查）。
        """
        if not (SCORE_MIN <= score <= SCORE_MAX):
            raise ValueError(f"match_score 越界：{score}（应在 {SCORE_MIN}..{SCORE_MAX}）")
        if not self._db.jobs().set_match_score(
            job_id, score, model=model, prompt_version=prompt_version, scored_at=scored_at
        ):
            return None
        return self._db.jobs().get(job_id)

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
