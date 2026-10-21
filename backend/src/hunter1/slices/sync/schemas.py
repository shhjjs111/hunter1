"""sync 切片的线上形状 —— 手机与电脑之间唯一的交换格式。

字段全部使用与现有 API 一致的 `snake_case`，并刻意**不带** `id` 之外的两端
内部字段（`company_id`、`title_key`）。跨设备真正稳定的是：

- 岗位：`detail_url`（同一岗位在不同设备上的 `id` 可能不同）
- 投递：`job_detail_url`（投递记录的本体是「对哪个岗位做了什么」）

因此同步按 URL 去重，而不是按 UUID 去重。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hunter1.domain.models import ApplicationStage, CaptureStatus
from hunter1.slices.scoring import (
    MAX_PROFILE_DIRECTIONS,
    MAX_PROFILE_ITEM_CHARS,
    MAX_PROFILE_KEYWORDS,
    MAX_PROFILE_SUMMARY_CHARS,
    CandidateProfile,
)

#: JD 正文上限，与后端抓取写入的同一量级（见 `platform/fetch/http.py`）。
MAX_JD_CHARS = 200_000
#: 评分类文本上限，与 `scoring.store.MAX_SCORE_TEXT_CHARS` 一致。
MAX_SCORE_TEXT_CHARS = 2_000
#: 投递备注上限，与 `applications.schemas.MAX_NOTE_CHARS` 一致。
MAX_NOTE_CHARS = 2_000


class _SyncModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyncJob(_SyncModel):
    """一台设备上的岗位事实 + 评分结论（**导出形状**）。

    这一层的字段刻意不设长度上限、也不校验 `detail_url` 非空：库里可能存着
    「截断提示让正文刚好超过上限」的 JD、历史遗留的超长结论，或者详情 URL 为空
    的岗位。导出是「把本机事实搬出去」，任何一条坏数据都不该让整份快照 500。
    需要写库的校验留给导入模型（`SyncJobImport`），在事务里动任何一行之前完成。
    """

    id: str
    title: str
    company: str
    city: str | None = None
    source: str = ""
    detail_url: str = ""
    jd_raw: str | None = None
    capture_status: CaptureStatus = CaptureStatus.UNKNOWN
    match_score: int | None = Field(default=None, ge=0, le=100)
    score_summary: str | None = None
    score_advantages: str | None = None
    score_gaps: str | None = None
    score_model: str | None = None
    score_prompt_version: str | None = None
    scored_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class SyncJobImport(SyncJob):
    """合并请求里的岗位：在导出形状上重新加回写库前的全部校验。

    导出要容忍历史坏数据，但**导入**会写进本机库，不能把超长正文、空标题或
    越界字段带进来。上限与各切片自己的表单 / 落库约束保持一致。
    """

    id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=512)
    company: str = Field(min_length=1, max_length=255)
    city: str | None = Field(default=None, max_length=255)
    source: str = Field(default="", max_length=128)
    detail_url: str = Field(min_length=1, max_length=2048)
    jd_raw: str | None = Field(default=None, max_length=MAX_JD_CHARS)
    score_summary: str | None = Field(default=None, max_length=MAX_SCORE_TEXT_CHARS)
    score_advantages: str | None = Field(default=None, max_length=MAX_SCORE_TEXT_CHARS)
    score_gaps: str | None = Field(default=None, max_length=MAX_SCORE_TEXT_CHARS)
    score_model: str | None = Field(default=None, max_length=128)
    score_prompt_version: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _seen_order(self) -> SyncJobImport:
        if self.first_seen_at is None or self.last_seen_at is None:
            return self
        try:
            out_of_order = self.first_seen_at > self.last_seen_at
        except TypeError as exc:
            raise ValueError("first_seen_at / last_seen_at 的时区必须一致") from exc
        if out_of_order:
            raise ValueError("first_seen_at 不能晚于 last_seen_at")
        return self


class SyncApplication(_SyncModel):
    """一条投递记录（按 `job_detail_url` 跨设备对齐；导出形状）。"""

    job_detail_url: str | None = None
    title: str
    company: str
    stage: ApplicationStage = ApplicationStage.APPLIED
    note: str | None = None
    applied_at: datetime
    updated_at: datetime


class SyncApplicationImport(SyncApplication):
    """合并请求里的投递：在导出形状上重新加回长度与时间先后校验。"""

    job_detail_url: str | None = Field(default=None, max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    company: str = Field(min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)

    @model_validator(mode="after")
    def _time_order(self) -> SyncApplicationImport:
        try:
            out_of_order = self.applied_at > self.updated_at
        except TypeError as exc:
            raise ValueError("applied_at / updated_at 的时区必须一致") from exc
        if out_of_order:
            raise ValueError("applied_at 不能晚于 updated_at")
        return self


class SyncProfile(_SyncModel):
    """候选人画像及其最后修改时间。"""

    keywords: list[str] = Field(default_factory=list, max_length=MAX_PROFILE_KEYWORDS)
    directions: list[str] = Field(default_factory=list, max_length=MAX_PROFILE_DIRECTIONS)
    summary: str = Field(default="", max_length=MAX_PROFILE_SUMMARY_CHARS)
    updated_at: datetime

    def to_candidate(self) -> CandidateProfile:
        """转成领域画像；三项全空时抛 ValueError（与配置页同一不变量）。"""
        for item in (*self.keywords, *self.directions):
            if len(item) > MAX_PROFILE_ITEM_CHARS:
                raise ValueError(f"画像条目超过 {MAX_PROFILE_ITEM_CHARS} 字符")
        return CandidateProfile(
            keywords=self.keywords,
            directions=self.directions,
            summary=self.summary,
        )


class SyncSnapshot(_SyncModel):
    """一台设备的完整可同步数据（**导出形状**）。"""

    version: Literal[1] = 1
    exported_at: datetime
    jobs: list[SyncJob] = Field(default_factory=list)
    applications: list[SyncApplication] = Field(default_factory=list)
    profile: SyncProfile | None = None


class SyncSnapshotImport(_SyncModel):
    """合并请求体：字段与导出形状一致，但保留时间先后校验。"""

    version: Literal[1] = 1
    exported_at: datetime
    jobs: list[SyncJobImport] = Field(default_factory=list)
    applications: list[SyncApplicationImport] = Field(default_factory=list)
    profile: SyncProfile | None = None


class SyncCounts(_SyncModel):
    """一次合并的计数（两端各自统计自己的库发生了什么）。"""

    jobs_created: int = 0
    jobs_updated: int = 0
    jobs_kept: int = 0
    applications_created: int = 0
    applications_updated: int = 0
    applications_kept: int = 0
    applications_skipped: int = 0
    profile_updated: bool = False


class SyncMergeResponse(_SyncModel):
    """合并结果 + 合并后的规范快照（调用方无需再拉一次）。"""

    counts: SyncCounts
    snapshot: SyncSnapshot


__all__ = [
    "MAX_JD_CHARS",
    "MAX_NOTE_CHARS",
    "MAX_SCORE_TEXT_CHARS",
    "SyncApplication",
    "SyncApplicationImport",
    "SyncCounts",
    "SyncJob",
    "SyncJobImport",
    "SyncMergeResponse",
    "SyncProfile",
    "SyncSnapshot",
    "SyncSnapshotImport",
]
