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

from hunter1.domain.text import normalize_job_title


class CaptureStatus(StrEnum):
    """岗位详情的抓取状态。"""

    UNKNOWN = "unknown"
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


class _Model(BaseModel):
    """项目内模型的统一基类：拒绝未知字段，避免拼写错误静默通过。"""

    model_config = ConfigDict(extra="forbid")


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
    city: str | None = None
    jd_raw: str | None = None
    match_score: int | None = Field(default=None, ge=0, le=100)
    capture_status: CaptureStatus = CaptureStatus.UNKNOWN
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def title_key(self) -> str:
        """标题归一化投影，用于同题折叠（见 domain.text）。"""
        return normalize_job_title(self.title)

    @model_validator(mode="after")
    def _enforce_seen_order(self) -> Job:
        if (
            self.first_seen_at is not None
            and self.last_seen_at is not None
            and self.first_seen_at > self.last_seen_at
        ):
            raise ValueError("first_seen_at must not be later than last_seen_at")
        return self


__all__ = ["CaptureStatus", "Company", "Job"]
