"""crawl 切片的 HTTP 面 —— 纯 JSON API（`/api` 前缀由组装处添加）。

组装处调用 `build_router(runner=...)` 注入依赖；切片不 import web。

进度用**轮询**（GET /crawl/status）而不是 SSE：与旧界面语义一致
（前端 setInterval 拉快照），且运行器本身就是「快照」模型 ——
改推流是另一件事，不在迁移范围内（见 SLICE.md 迁移注）。
"""

from __future__ import annotations

from fastapi import APIRouter

from hunter1.slices.crawl.runner import CrawlRunner


def build_router(*, runner: CrawlRunner) -> APIRouter:
    """构造 crawl 的 APIRouter（依赖由组装处注入）。"""
    router = APIRouter()

    @router.post("/crawl", summary="启动一轮抓取")
    def start_crawl() -> dict[str, bool]:
        # 已有任务在跑时 start() 返回 False —— 不抛错，让调用方按 started 分支处理
        return {"started": runner.start()}

    @router.get("/crawl/status", summary="抓取进度快照")
    def crawl_status() -> dict[str, object]:
        return runner.snapshot().as_dict()

    return router


__all__ = ["build_router"]
