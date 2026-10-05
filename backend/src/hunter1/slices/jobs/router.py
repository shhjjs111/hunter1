"""jobs 切片的 HTTP 面 —— 纯 JSON API（`/api` 前缀由组装处添加）。

组装处调用 `build_router(store=..., clock=...)` 注入依赖；切片不 import
web（依赖方向 web → slices）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

from hunter1.slices.jobs import schemas, service
from hunter1.slices.jobs.store import JobStore


def _now() -> datetime:
    return datetime.now(UTC)


def build_router(*, store: JobStore, clock: Callable[[], datetime] | None = None) -> APIRouter:
    """构造 jobs 的 APIRouter（依赖由组装处注入）。"""
    router = APIRouter()
    now = clock or _now

    @router.get("/jobs", response_model=schemas.JobListResponse, summary="岗位列表（搜索+分页）")
    def list_jobs(
        q: str = Query("", description="关键词（标题归一化匹配；空 = 列最近）"),
        page: int = Query(1, ge=1, description="页码（1 起，上限 10000）"),
        page_size: int = Query(20, ge=1, le=service.MAX_PAGE_SIZE),
    ) -> schemas.JobListResponse:
        result = service.list_jobs(store, keyword=q, page=page, page_size=page_size)
        return schemas.JobListResponse(
            items=[schemas.JobSummary.from_job(job) for job in result.items],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            has_next=result.has_next,
        )

    @router.get(
        "/jobs/{job_id}", response_model=schemas.JobDetail, summary="岗位详情（全 id 或唯一前缀）"
    )
    def job_detail(job_id: str) -> schemas.JobDetail:
        job, ambiguous = service.find_job(store, job_id)
        if ambiguous:
            raise HTTPException(
                status_code=409,
                detail=f"id 前缀 {job_id} 有 {ambiguous} 条匹配，请给更长的 id",
            )
        if job is None:
            raise HTTPException(status_code=404, detail=f"岗位不存在：{job_id}")
        return schemas.JobDetail.from_job(job)

    @router.post(
        "/jobs/{job_id}/apply",
        response_model=schemas.ApplyResponse,
        status_code=201,
        summary="记录投递",
    )
    def job_apply(job_id: str) -> schemas.ApplyResponse:
        job, ambiguous = service.find_job(store, job_id)
        if ambiguous:
            raise HTTPException(
                status_code=409,
                detail=f"id 前缀 {job_id} 有 {ambiguous} 条匹配，请给更长的 id",
            )
        if job is None:
            raise HTTPException(status_code=404, detail=f"岗位不存在：{job_id}")
        application = service.apply_to_job(store=store, job=job, now=now())
        return schemas.ApplyResponse(application_id=application.id)

    return router


__all__ = ["build_router"]
