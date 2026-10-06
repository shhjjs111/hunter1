"""jobs 切片的用例编排 —— 只依赖 store 与领域模型，不碰 HTTP。

分页与搜索的行为与旧版 Web 页面**逐字一致**（`_MAX_PAGE` 钳制、空关键词
列最近、关键词走归一化匹配）—— 迁移不改变行为，只改变形状。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hunter1.domain.models import Application, Job
from hunter1.slices.jobs.store import JobStore

# 页码上界：只设下界时 page=999999 会变成天量 OFFSET（SQLite 要扫描并丢弃
# 前面所有行才能定位）。真实使用没人会翻到第 10000 页，但「无限」由此变「有界」。
MAX_PAGE = 10_000
MAX_PAGE_SIZE = 100


@dataclass
class JobPage:
    """一页岗位及其分页元信息。"""

    items: list[Job]
    total: int
    page: int
    page_size: int
    has_next: bool


def list_jobs(store: JobStore, *, keyword: str = "", page: int = 1, page_size: int = 20) -> JobPage:
    """列出/搜索岗位（页码有界，页大小有界）。"""
    size = max(1, min(page_size, MAX_PAGE_SIZE))
    page = min(max(1, page), MAX_PAGE)
    offset = (page - 1) * size
    cleaned = keyword.strip()

    items = store.page(keyword=cleaned, limit=size, offset=offset)
    total = store.count(keyword=cleaned)
    return JobPage(
        items=items,
        total=total,
        page=page,
        page_size=size,
        # 与旧实现同一判据：用实际取回条数判断，而不是 page*size < total
        has_next=offset + len(items) < total,
    )


def find_job(store: JobStore, job_id: str) -> tuple[Job | None, int]:
    """按 id（或唯一前缀）找岗位。返回 (命中, 前缀歧义数)。

    - 全 id 命中 → (job, 0)
    - 前缀唯一命中 → (job, 0)
    - 前缀多命中 → (None, n)（调用方据此提示「给更长的 id」）
    - 未命中 → (None, 0)
    """
    job = store.get(job_id)
    if job is not None:
        return job, 0
    candidates = store.get_by_prefix(job_id)
    if len(candidates) == 1:
        return candidates[0], 0
    if len(candidates) > 1:
        return None, len(candidates)
    return None, 0


def apply_to_job(*, store: JobStore, job: Job, now: datetime) -> Application:
    """记录一条投递（公司名与标题快照下来，见 application.applications）。

    **幂等**：同一岗位已投递过则返回既有记录，不新建 —— 重复点击不该堆出多条
    投递（旧实现每次都 `new_application`，重复点击即重复记录）。

    迁移注：`new_application` 目前来自旧 application 层；Wave 4 后改经
    applications 切片的公开面（依赖已在 test_architecture 的白名单登记）。
    """
    existing = store.find_application_by_job(job.id)
    if existing is not None:
        return existing

    from hunter1.application.applications import new_application

    application = new_application(job=job, now=now)
    store.save_application(application)
    return application


__all__ = ["MAX_PAGE", "MAX_PAGE_SIZE", "JobPage", "apply_to_job", "find_job", "list_jobs"]
