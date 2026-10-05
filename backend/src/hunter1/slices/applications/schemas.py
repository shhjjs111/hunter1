"""applications 切片的 API 模型 —— 跨端契约的**唯一事实来源**。

这些 Pydantic 模型经 OpenAPI 快照（`contracts/openapi.json`）流向
前端类型（`frontend/src/shared/api/schema.d.ts`）。改这里 = 改契约：
改完跑 `bash scripts/contracts.sh` 重新导出并提交快照。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from hunter1.domain.models import Application


class ApplicationSummary(BaseModel):
    """投递列表项。"""

    id: str
    job_id: str
    company: str
    title: str
    stage: str
    applied_at: datetime
    updated_at: datetime
    note: str | None = None

    @classmethod
    def from_application(cls, application: Application) -> ApplicationSummary:
        return cls(
            id=application.id,
            job_id=application.job_id,
            company=application.company,
            title=application.title,
            stage=application.stage.value,
            applied_at=application.applied_at,
            updated_at=application.updated_at,
            note=application.note,
        )


class StageUpdateRequest(BaseModel):
    """推进投递阶段的请求体（`stage` 为 `ApplicationStage` 的字面值）。"""

    stage: str
    note: str | None = None


class StageUpdateResponse(BaseModel):
    """推进投递阶段的结果。"""

    application_id: str
    stage: str


class ApplicationListResponse(BaseModel):
    """投递列表页。"""

    items: list[ApplicationSummary]


__all__ = [
    "ApplicationListResponse",
    "ApplicationSummary",
    "StageUpdateRequest",
    "StageUpdateResponse",
]
