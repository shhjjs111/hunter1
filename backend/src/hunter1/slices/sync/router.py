"""sync 切片的 HTTP 面 —— 快照导出与合并。

两条端点都返回**有类型**的模型（不是裸 dict）：手机客户端的 TS 类型由同一份
OpenAPI 契约生成，字段名一旦漂移会在契约门禁处被发现。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from hunter1.slices.sync.schemas import SyncMergeResponse, SyncSnapshot, SyncSnapshotImport
from hunter1.slices.sync.store import SyncStore

#: 单次同步请求的上限。快照是用户自己的求职数据，正常远小于这些数字；
#: 设上限是为了不让一个畸形请求把服务端拖进长时间循环或吃光内存。
MAX_SYNC_BODY_BYTES = 64 * 1024 * 1024
MAX_SYNC_JOBS = 50_000
MAX_SYNC_APPLICATIONS = 50_000


def _reject_oversized_body(request: Request) -> None:
    """在解析 JSON 之前挡掉超大请求（有 Content-Length 时）。"""
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        length = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Content-Length 不合法") from exc
    if length > MAX_SYNC_BODY_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"同步快照超过 {MAX_SYNC_BODY_BYTES // (1024 * 1024)} MiB 上限",
        )


def build_router(*, store: SyncStore) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/sync/snapshot",
        response_model=SyncSnapshot,
        summary="导出本机可同步数据（岗位 / 投递 / 候选人画像）",
    )
    def snapshot() -> SyncSnapshot:
        return store.export_snapshot()

    @router.post(
        "/sync/merge",
        response_model=SyncMergeResponse,
        summary="合并另一台设备的快照，并返回合并后的规范快照",
        dependencies=[Depends(_reject_oversized_body)],
        responses={
            413: {"description": "请求体超过大小上限"},
            422: {
                "description": "快照条目过多，或画像不合法（例如三项全空或条目超长）",
            },
        },
    )
    def merge(body: SyncSnapshotImport) -> SyncMergeResponse:
        if len(body.jobs) > MAX_SYNC_JOBS or len(body.applications) > MAX_SYNC_APPLICATIONS:
            raise HTTPException(
                status_code=422,
                detail=(
                    "同步快照条目过多："
                    f"岗位最多 {MAX_SYNC_JOBS} 条，投递最多 {MAX_SYNC_APPLICATIONS} 条"
                ),
            )
        try:
            counts, merged = store.merge_snapshot(body)
        except ValueError as exc:
            # 画像不合法是**用户可修复**的输入问题，不是服务端 500。
            raise HTTPException(status_code=422, detail=f"同步数据不合法：{exc}") from exc
        return SyncMergeResponse(counts=counts, snapshot=merged)

    return router


__all__ = ["build_router"]
