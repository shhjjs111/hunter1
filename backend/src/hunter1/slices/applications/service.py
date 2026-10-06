"""applications 切片的用例编排 —— 依赖领域模型与本切片 store，不碰 HTTP。

行为与旧 `application/applications.py` **逐字一致**：公司名与标题在投递时
**快照**下来 —— 岗位是外部世界的快照（会被重抓、改名、下线），投递是
「我做过的事」，前者变了不该篡改后者的历史。`new_application` / `change_stage`
只依赖领域类型，因此可以完全离线测试。

「投递」这一动作归本切片所有：入口 `apply_to_job` 与记录本体在同一个领地，
jobs 切片只提供岗位查询（依赖方向 `applications → jobs` 是白名单允许的）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from hunter1.domain.models import Application, ApplicationStage, Job
from hunter1.slices.applications.store import ApplicationStore


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


def apply_to_job(
    *, store: ApplicationStore, job: Job, now: datetime, note: str | None = None
) -> Application:
    """记录一条投递（公司名与标题快照下来）。

    **幂等**：同一岗位已投递过则返回既有记录，不新建 —— 重复点击不该堆出多条
    投递。判据是「该岗位有没有投递记录」，不是「最近一条投递是不是这个岗位」。
    """
    existing = store.by_job(job.id)
    if existing:
        return existing[0]

    application = new_application(job=job, now=now, note=note)
    store.upsert(application)
    return application


__all__ = ["apply_to_job", "change_stage", "new_application"]
