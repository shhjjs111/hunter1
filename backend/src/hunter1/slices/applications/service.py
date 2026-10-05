"""applications 切片的用例编排 —— 只依赖领域模型，不碰 HTTP 也不碰数据库。

行为与旧 `application/applications.py` **逐字一致**：公司名与标题在投递时
**快照**下来 —— 岗位是外部世界的快照（会被重抓、改名、下线），投递是
「我做过的事」，前者变了不该篡改后者的历史。用例只依赖领域类型，因此可以
完全离线测试；仓储由调用方注入。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from hunter1.domain.models import Application, ApplicationStage, Job


def new_application(
    *,
    job: Job,
    now: datetime,
    stage: ApplicationStage = ApplicationStage.APPLIED,
    note: str | None = None,
    application_id: str | None = None,
) -> Application:
    """按岗位建立一条投递记录。"""
    return Application(
        id=application_id or uuid4().hex,
        job_id=job.id,
        company=job.company_name or job.source,
        title=job.title,
        stage=stage,
        applied_at=now,
        updated_at=now,
        note=note,
    )


def change_stage(
    application: Application,
    *,
    stage: ApplicationStage,
    now: datetime,
    note: str | None = None,
) -> Application:
    """推进到新阶段；`note` 不传则保留原备注。

    刻意不走 `model_copy(update=...)`：pydantic v2 的 `model_copy` **不重跑校验器**，
    那样「把更新时间设到投递之前」这种非法状态会被静默造出来。这里走一次
    `model_validate`，让领域不变量仍然兜底。
    """
    payload = application.model_dump()
    payload.update(
        {
            "stage": stage,
            "updated_at": now,
            "note": application.note if note is None else note,
        }
    )
    return Application.model_validate(payload)


__all__ = ["change_stage", "new_application"]
