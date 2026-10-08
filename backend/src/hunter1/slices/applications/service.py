"""applications 切片的用例编排 —— 依赖领域模型与本切片 store，不碰 HTTP。

设计要点是**快照不变量**：公司名与标题在投递那一刻抄下来。岗位是外部世界的
快照（会被重抓、改名、下线），投递是「我做过的事」—— 前者变了不该篡改后者的
历史，所以投递记录不回头读岗位，也就不会随岗位改版而失真。

`new_application` / `change_stage` 只依赖领域类型，因此可以完全离线测试。

「投递」这一动作归本切片所有：入口 `apply_to_job` 与记录本体在同一个领地，
jobs 切片只提供岗位查询（依赖方向 `applications → jobs` 是白名单允许的）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from hunter1.domain.models import Application, ApplicationStage, Job
from hunter1.slices.applications.store import ApplicationStore


@dataclass(frozen=True)
class ApplyOutcome:
    """一次「记录投递」的结果。

    `created=False` = 该岗位已有投递记录（幂等命中或并发下别人先落库）——
    路由据此决定返回 201 还是 200：状态码谎称「新建了资源」会让调用方做出
    错误的后续动作（例如再弹一次「已记录」提示、或以为产生了新 id）。
    """

    application: Application
    created: bool


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
) -> ApplyOutcome:
    """记录一条投递（公司名与标题快照下来）。

    **幂等**：同一岗位已投递过则返回既有记录，不新建 —— 重复点击不该堆出多条
    投递。判据是「该岗位有没有投递记录」，不是「最近一条投递是不是这个岗位」。

    ⚠ 先读后写**本身挡不住并发**（路由是同步 `def`，FastAPI 放线程池真并行：两个
    请求都读到空、各插一条）。所以写入走 `store.insert_for_job` —— 它由数据库的
    `UNIQUE(job_id)` 兜底，撞了就返回先落库的那条。这里保留先读只是快速路径
    （绝大多数请求是重复点击，一次查询就能返回）。
    """
    existing = store.by_job(job.id)
    if existing:
        return ApplyOutcome(application=existing[0], created=False)

    candidate = new_application(job=job, now=now, note=note)
    stored = store.insert_for_job(candidate)
    # 落库回来的若不是我们构造的那条，说明并发下别人先插了 —— 那次不算「新建」。
    return ApplyOutcome(application=stored, created=stored.id == candidate.id)


__all__ = ["ApplyOutcome", "apply_to_job", "change_stage", "new_application"]
