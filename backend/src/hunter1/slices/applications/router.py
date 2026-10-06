"""applications 切片的 HTTP 面 —— 纯 JSON API（`/api` 前缀由组装处添加）。

组装处调用 `build_router(store=..., clock=...)` 注入依赖；切片不 import
web（依赖方向 web → slices）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response

from hunter1.slices.applications import schemas, service
from hunter1.slices.applications.store import ApplicationStore

# 列表固定上限：投递是「我做过的事」，量级有限；无分页参数，取最近 200 条。
LIST_LIMIT = 200


def _now() -> datetime:
    return datetime.now(UTC)


def build_router(
    *, store: ApplicationStore, clock: Callable[[], datetime] | None = None
) -> APIRouter:
    """构造 applications 的 APIRouter（依赖由组装处注入）。"""
    router = APIRouter()
    now = clock or _now

    @router.get(
        "/applications",
        response_model=schemas.ApplicationListResponse,
        summary="投递列表",
    )
    def list_applications() -> schemas.ApplicationListResponse:
        items = store.list(limit=LIST_LIMIT)
        return schemas.ApplicationListResponse(
            items=[schemas.ApplicationSummary.from_application(item) for item in items]
        )

    @router.post(
        "/applications/{application_id}/stage",
        response_model=schemas.StageUpdateResponse,
        summary="推进投递阶段",
    )
    def update_stage(
        application_id: str, body: schemas.StageUpdateRequest
    ) -> schemas.StageUpdateResponse:
        application = store.get(application_id)
        if application is None:
            raise HTTPException(status_code=404, detail=f"投递不存在：{application_id}")
        # stage 已由请求体校验成合法枚举（非法值在进入本函数之前就是 422）
        updated = service.change_stage(application, stage=body.stage, now=now(), note=body.note)
        store.upsert(updated)
        return schemas.StageUpdateResponse(application_id=updated.id, stage=updated.stage.value)

    @router.delete("/applications/{application_id}", status_code=204, summary="删除投递")
    def delete_application(application_id: str) -> Response:
        store.delete(application_id)
        return Response(status_code=204)

    return router


__all__ = ["LIST_LIMIT", "build_router"]
