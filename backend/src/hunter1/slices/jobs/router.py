"""jobs 切片的 HTTP 面 —— 纯 JSON API（`/api` 前缀由组装处添加）。

组装处调用 `build_router(store=...)` 注入依赖；切片不 import web
（依赖方向 web → slices）。

**不注入时钟**：本切片只剩只读端点，不写时间戳 —— 「记录投递」连同它的
时钟一起归了 applications 切片。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from hunter1.slices.jobs import schemas, service
from hunter1.slices.jobs.store import JobStore


def build_router(*, store: JobStore) -> APIRouter:
    """构造 jobs 的 APIRouter（依赖由组装处注入）。"""
    router = APIRouter()

    @router.get("/jobs", response_model=schemas.JobListResponse, summary="岗位列表（搜索+分页）")
    def list_jobs(
        q: str = Query("", description="关键词（标题归一化匹配；空 = 列最近）"),
        # `le=service.MAX_PAGE` 与 `page_size` 的 `le` 对称：服务层虽有 `min(max(...))`
        # 兜底，但那会让 page=999999 静默变成第 10000 页、响应里 `page` 却是 10000，
        # 而调用方从契约（OpenAPI 的 description 只是字符串）读不到任何上限。边界处
        # 拒绝比悄悄改语义好 —— 与 `page_size` 一致。
        page: int = Query(1, ge=1, le=service.MAX_PAGE, description="页码（1 起）"),
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
                detail=f"id 前缀 {job_id} 至少有 {ambiguous} 条匹配，请给更长的 id",
            )
        if job is None:
            raise HTTPException(status_code=404, detail=f"岗位不存在：{job_id}")
        return schemas.JobDetail.from_job(job)

    return router


__all__ = ["build_router"]
