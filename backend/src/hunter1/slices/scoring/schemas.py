"""scoring 切片的 HTTP 形状 —— **API 形状的唯一事实来源**（契约源头）。

切片里有两个类型层，别混：

- `models.py`：**领域**模型（画像 / 评分卡），无 IO，被 service 与 prompts 使用；
- 本文件：**线上面**（请求体 / 响应体），被 router 使用。

路由返回这里的模型而不是裸 `dict`：裸 dict 会让 OpenAPI 退化成
`additionalProperties: true`，前端拿不到类型 —— 契约在最有价值的地方断掉
（其余切片的响应都是有类型的，见 `crawl/router.py` 的注释）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hunter1.slices.scoring.models import CandidateProfile


class ProfileForm(BaseModel):
    """`PUT /api/scoring/profile` 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list)
    directions: list[str] = Field(default_factory=list)
    summary: str = ""

    def to_profile(self) -> CandidateProfile:
        """转成领域画像。

        三项全空时 `CandidateProfile` 会拒绝（它的不变量：至少要有一项信号）——
        调用方把这个失败映射成 422 并给出可行动的说明。
        """
        return CandidateProfile(
            keywords=self.keywords,
            directions=self.directions,
            summary=self.summary,
        )


class ProfileView(BaseModel):
    """`GET` / `PUT /api/scoring/profile` 的响应。

    「没配过」用 `profile: null` 表达，而不是 404：未配置是**初始状态**，不是错误。
    界面据此渲染空表单。

    `warning` 用于「存储里的画像不合法」（例如按旧规则存下的超大画像，或数据被
    改坏）：此时 `profile` 给 `null` **并**说明原因 —— 既不让用户撞上 500 而无法
    打开配置页去修，也不静默假装没配过（那会掩盖损坏、让用户填过的内容无声消失）。
    """

    profile: CandidateProfile | None = None
    warning: str | None = None


class ScoreView(BaseModel):
    """`POST` / `GET /api/scoring/{job_id}` 的响应。

    模型算出来的三段结论文本全在这里，**并且已落库**（见 `store.save_score`）——
    原先 `advantages` / `gaps` 连响应都没进，`summary` 也只是闪一下：刷新页面即无据
    可查，而模型已经为此花过钱。
    """

    job_id: str
    score: int
    summary: str | None = None
    advantages: str | None = None
    gaps: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    scored_at: datetime | None = None


__all__ = ["ProfileForm", "ProfileView", "ScoreView"]
