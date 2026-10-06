"""applications 切片的 API 模型 —— 跨端契约的**唯一事实来源**。

这些 Pydantic 模型经 OpenAPI 快照（`contracts/openapi.json`）流向
前端类型（`frontend/src/shared/api/schema.d.ts`）。改这里 = 改契约：
改完跑 `bash scripts/contracts.sh` 重新导出并提交快照。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from hunter1.domain.models import Application, ApplicationStage


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
    """推进投递阶段的请求体。

    `stage` 直接用 `ApplicationStage` 而非 `str`：OpenAPI 快照里会输出**真正的
    enum**（前端可照契约比对，不再靠魔法字符串对齐一份手抄的取值表），非法值由
    请求体校验直接拒为 422 —— 路由不必再手工判一次、也就不会漏判。
    """

    stage: ApplicationStage
    note: str | None = None


class StageUpdateResponse(BaseModel):
    """推进投递阶段的结果。"""

    application_id: str
    stage: str


class ApplicationListResponse(BaseModel):
    """投递列表页。"""

    items: list[ApplicationSummary]


class CreateApplicationRequest(BaseModel):
    """记录一次投递的请求体。

    `job_id` 支持全 id 或唯一前缀（与岗位详情端点同一套解析）—— 助手常只看到
    前 8 位 id，界面给的是全 id，两者都得能用。
    """

    job_id: str


class ApplyResponse(BaseModel):
    """记录投递的结果。"""

    application_id: str


__all__ = [
    "ApplicationListResponse",
    "ApplicationSummary",
    "ApplyResponse",
    "CreateApplicationRequest",
    "StageUpdateRequest",
    "StageUpdateResponse",
]
