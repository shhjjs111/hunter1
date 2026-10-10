"""领域模型 —— 纯数据与不变量，**无任何 IO 依赖**。

设计要点：
- 用 pydantic 做校验，把「不变量」写在模型里（而非散落在调用方）。
- `Job.title_key` 是 `title` 的归一化**投影**（computed），保证同题折叠的一致性，
  避免调用方各自实现归一化。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from hunter1.platform.text import normalize_job_title


class CaptureStatus(StrEnum):
    """岗位详情的抓取状态。"""

    UNKNOWN = "unknown"
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


class ApplicationStage(StrEnum):
    """投递所处阶段。顺序即流程顺序，界面按此排进度。"""

    APPLIED = "applied"
    WRITTEN_TEST = "written_test"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class _Model(BaseModel):
    """项目内模型的统一基类：拒绝未知字段，避免拼写错误静默通过。"""

    model_config = ConfigDict(extra="forbid")


def _ensure_order(earlier: datetime, later: datetime, message: str) -> None:
    """断言 `earlier <= later`，并把时区awareness 混用翻译成可读的 ValidationError。

    naive 与 aware 混用时，裸比较抛的是 `TypeError` —— pydantic **不会**把它
    包成 `ValidationError`，所以校验器里这么写会让错误直穿成 500，而调用方
    按 `ValidationError`（422）处理就会漏掉。这里统一翻成 `ValueError`
    （→ ValidationError），失败模式从「服务端 500」变成「请求 422 + 可读原因」。
    """
    try:
        out_of_order = earlier > later
    except TypeError as exc:
        raise ValueError(
            f"时间字段时区不一致（naive 与 aware 混用，应统一为带时区）：{message}"
        ) from exc
    if out_of_order:
        raise ValueError(message)


class Company(_Model):
    """一家用人单位及其抓取绑定。"""

    id: str
    name: str
    source: str
    source_ref: str | None = None
    aliases: list[str] = Field(default_factory=list)
    campus_url: str | None = None
    crawler_key: str | None = None


class Job(_Model):
    """一个岗位及其抓取/评分状态。"""

    id: str
    company_id: str
    title: str
    detail_url: str
    source: str
    source_ref: str | None = None
    # `company_id` 是身份哈希（用于关联与折叠），人读不懂；界面要显示的公司名
    # 单独留一列 —— 聚合站里每条岗位的公司都不同，不给名字就没法展示。
    company_name: str | None = None
    city: str | None = None
    jd_raw: str | None = None
    match_score: int | None = Field(default=None, ge=0, le=100)
    # 评分的**结论文本**：模型算出来的优势 / 差距 / 摘要。
    # 原先它们只在 HTTP 响应里闪一下就没了（`advantages` / `gaps` 连响应都没进），
    # 刷新页面即无据可查 —— 而模型已经为此花过钱。与 `match_score` 同属评分切片
    # 拥有的列，抓取路径不写它们（见 `repository._assign_facts`）。
    score_summary: str | None = None
    score_advantages: str | None = None
    score_gaps: str | None = None
    # 溯源：这一版分是哪个模型、哪版提示词打的，以及什么时候打的。
    score_model: str | None = None
    score_prompt_version: str | None = None
    scored_at: datetime | None = None
    capture_status: CaptureStatus = CaptureStatus.UNKNOWN
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def title_key(self) -> str:
        """标题归一化投影，用于同题折叠（见 platform.text）。"""
        return normalize_job_title(self.title)

    @model_validator(mode="after")
    def _enforce_seen_order(self) -> Job:
        if self.first_seen_at is not None and self.last_seen_at is not None:
            _ensure_order(
                self.first_seen_at,
                self.last_seen_at,
                "first_seen_at must not be later than last_seen_at",
            )
        return self


class Application(_Model):
    """一条投递记录。

    与 `Job` 是两件事：岗位是「世界上有什么」，投递是「我做了什么」。
    因此只留一个 `job_id` 引用（岗位可能被重抓、改名），**冗余存下公司名与标题** ——
    岗位库清空或岗位被下线时，投递记录仍要能读懂。
    """

    id: str
    job_id: str
    company: str
    title: str
    stage: ApplicationStage = ApplicationStage.APPLIED
    applied_at: datetime
    updated_at: datetime
    note: str | None = None

    @model_validator(mode="after")
    def _enforce_time_order(self) -> Application:
        _ensure_order(
            self.applied_at, self.updated_at, "applied_at must not be later than updated_at"
        )
        return self


__all__ = ["Application", "ApplicationStage", "CaptureStatus", "Company", "Job"]
