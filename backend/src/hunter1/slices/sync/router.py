"""sync 切片的 HTTP 面 —— 快照导出与合并。

两条端点都返回**有类型**的模型（不是裸 dict）：手机客户端的 TS 类型由同一份
OpenAPI 契约生成，字段名一旦漂移会在契约门禁处被发现。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from hunter1.slices.sync.schemas import SyncMergeResponse, SyncSnapshot
from hunter1.slices.sync.store import SyncStore


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
        responses={
            422: {
                "description": "快照里的画像不合法（例如三项全空或条目超长）",
            },
        },
    )
    def merge(body: SyncSnapshot) -> SyncMergeResponse:
        try:
            counts, merged = store.merge_snapshot(body)
        except ValueError as exc:
            # 画像不合法是**用户可修复**的输入问题，不是服务端 500。
            raise HTTPException(status_code=422, detail=f"同步数据不合法：{exc}") from exc
        return SyncMergeResponse(counts=counts, snapshot=merged)

    return router


__all__ = ["build_router"]
