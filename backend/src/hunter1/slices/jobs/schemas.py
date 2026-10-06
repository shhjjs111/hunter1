"""jobs 切片的 API 模型 —— 跨端契约的**唯一事实来源**。

这些 Pydantic 模型经 OpenAPI 快照（`contracts/openapi.json`）流向
前端类型（`frontend/src/shared/api/schema.d.ts`）。改这里 = 改契约：
改完跑 `bash scripts/contracts.sh` 重新导出并提交快照。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from hunter1.domain.models import Job


class JobSummary(BaseModel):
    """岗位列表项。"""

    id: str
    title: str
    company: str
    city: str | None = None
    match_score: int | None = None
    source: str
    capture_status: str
    detail_url: str
    last_seen_at: datetime | None = None

    @classmethod
    def from_job(cls, job: Job) -> JobSummary:
        return cls(
            id=job.id,
            title=job.title,
            # 人读的公司名：company_id 是身份哈希，界面显示不了（见 domain/models.py）
            company=job.company_name or job.source,
            city=job.city,
            match_score=job.match_score,
            source=job.source,
            capture_status=job.capture_status.value,
            detail_url=job.detail_url,
            last_seen_at=job.last_seen_at,
        )


class JobDetail(JobSummary):
    """岗位详情（列表项 + JD 正文）。"""

    jd_raw: str | None = None

    @classmethod
    def from_job(cls, job: Job) -> JobDetail:
        base = JobSummary.from_job(job)
        return cls(**base.model_dump(), jd_raw=job.jd_raw)


class JobListResponse(BaseModel):
    """岗位列表页（搜索 + 分页）。"""

    items: list[JobSummary]
    total: int
    page: int
    page_size: int
    has_next: bool


__all__ = ["JobDetail", "JobListResponse", "JobSummary"]
