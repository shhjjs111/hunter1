"""crawl 切片的 HTTP 面 —— 纯 JSON API（`/api` 前缀由组装处添加）。

组装处调用 `build_router(runner=...)` 注入依赖；切片不 import web。

进度用**轮询**（GET /crawl/status）而不是 SSE：与旧界面语义一致
（前端 setInterval 拉快照），且运行器本身就是「快照」模型 ——
改推流是另一件事，不在迁移范围内（见 SLICE.md 的设计取舍）。

响应是**有类型的模型**（不是裸 dict）—— 否则 OpenAPI 生不出 schema，
前端拿不到类型，契约在最有价值的地方断了。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from hunter1.slices.crawl.runner import CrawlRunner
from hunter1.slices.crawl.schemas import CrawlStatusResponse, StartCrawlResponse


def build_router(*, runner: CrawlRunner) -> APIRouter:
    """构造 crawl 的 APIRouter（依赖由组装处注入）。"""
    router = APIRouter()

    @router.post("/crawl", response_model=StartCrawlResponse, summary="启动一轮抓取")
    def start_crawl() -> StartCrawlResponse:
        # 已在跑时 `start()` 返回 False —— 那不是错误，调用方按 `started` 分支处理。
        #
        # 唯一会让它抛错的是「线程起不来」（`RuntimeError: can't start new thread`，
        # 线程/句柄耗尽）。那**不是**请求的问题，而是**服务器资源**问题，所以给 503 +
        # 可读原因，而不是裸 500 —— 500 会让人拿着「实现 bug」的线索去查代码与端口契约。
        # 刻意只捕 `RuntimeError`：抛别的说明实现坏了，那该响亮地 500
        # （与 assistant `/turn` 对 LLMError 的取舍同源）。
        try:
            started = runner.start()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"抓取线程启动失败：{exc}") from exc
        return StartCrawlResponse(started=started)

    @router.get("/crawl/status", response_model=CrawlStatusResponse, summary="抓取进度快照")
    def crawl_status() -> CrawlStatusResponse:
        return CrawlStatusResponse.from_snapshot(runner.snapshot())

    return router


__all__ = ["build_router"]
