"""组装根 —— 把切片、静态资源与运行配置接成一个 ASGI 应用。

**组装处是唯一认识所有切片的地方**：切片自己不 import 彼此，也不知道
HTTP 之外的世界（见 AGENTS.md 的依赖方向表）。所有跨切片的接线都在这里。

两种运行形态（设计公理 3「双形态交付」）：

- **开发**：前端由 Vite 独立进程服务（:5173），经 proxy 打到这里（:8000）——
  本文件的静态资源部分不参与；
- **交付**：前端的构建产物（`frontend/dist`）由本文件直接服务，
  单进程单目录，`/api/*` 之外的所有路径回落到 `index.html`（SPA 路由）。

`AppContext` 也住在这里：它是「组装用的依赖包」，与组装根本身同一个生命周期。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from hunter1.application.ports import Crawler, LLMProvider, TextFetcher
from hunter1.domain.settings import LLMSettings
from hunter1.platform.db import Database
from hunter1.slices.applications.router import build_router as build_applications_router
from hunter1.slices.applications.store import ApplicationStore
from hunter1.slices.assistant.job_tools import build_tools
from hunter1.slices.assistant.router import build_router as build_assistant_router
from hunter1.slices.assistant.store import ConversationStore
from hunter1.slices.crawl.router import build_router as build_crawl_router
from hunter1.slices.crawl.runner import CrawlRunner
from hunter1.slices.jobs.router import build_router as build_jobs_router
from hunter1.slices.jobs.store import JobStore
from hunter1.slices.scoring.models import CandidateProfile
from hunter1.slices.scoring.router import build_router as build_scoring_router
from hunter1.slices.scoring.store import ScoreStore
from hunter1.slices.settings.router import build_router as build_settings_router
from hunter1.slices.settings.store import SettingsStore

API_PREFIX = "/api"


def _now() -> datetime:
    return datetime.now(UTC)


def _default_llm_factory(settings: LLMSettings) -> LLMProvider:
    """按配置构造 OpenAI 兼容客户端。

    延迟导入：不装 `web` 之外的依赖时，只要不碰这个函数就不必付出导入成本。
    """
    from hunter1.platform.llm import OpenAICompatibleClient

    return OpenAICompatibleClient(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        timeout=60.0,
    )


def frontend_dir() -> Path | None:
    """前端构建产物所在目录；不存在返回 None（开发态的正常情形）。

    解析顺序：
    1. `HUNTER1_FRONTEND_DIR` 环境变量（测试与联调用）；
    2. 打包态：PyInstaller 解包目录下的 `hunter1/web_dist`；
    3. 源码态：仓库的 `frontend/dist`。
    """
    override = os.environ.get("HUNTER1_FRONTEND_DIR")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None

    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = bundled / "hunter1" / "web_dist"
        return candidate if candidate.is_dir() else None

    # main.py → hunter1 → src → backend → 仓库根
    candidate = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    return candidate if candidate.is_dir() else None


@dataclass
class AppContext:
    """组装处依赖的一组能力（外部实现经此注入，便于测试替换）。"""

    db: Database
    fetcher: TextFetcher
    llm_factory: Callable[[LLMSettings], LLMProvider] = _default_llm_factory
    clock: Callable[[], datetime] = _now
    site_keys: list[str] | None = None
    assistant_history_limit: int = 20
    page_size: int = 20
    # 候选画像：评分的输入。为 None 时 scoring 的端点不挂载
    # （「没接线」表现为「端点不存在」，而不是「端点存在但总是报错」）。
    candidate_profile: CandidateProfile | None = None

    def crawler_factory(self) -> list[Crawler]:
        """构造本轮要跑的抓取器（默认全部注册站点）。"""
        from hunter1.slices.crawl.sites import build_all

        return build_all(fetcher=self.fetcher, keys=self.site_keys)

    @classmethod
    def default(
        cls,
        *,
        db_path: str | Path,
        site_keys: list[str] | None = None,
        fetcher: TextFetcher | None = None,
        candidate_profile: CandidateProfile | None = None,
    ) -> AppContext:
        """按本机默认配置装配（真实 SQLite + 真实 HTTP 抓取器）。"""
        database = Database(db_path)
        database.initialize()
        if fetcher is None:
            from hunter1.platform.fetch.http import HttpFetcher

            fetcher = HttpFetcher(timeout=20, retries=2)
        return cls(
            db=database,
            fetcher=fetcher,
            site_keys=site_keys,
            candidate_profile=candidate_profile,
        )


def create_app(context: AppContext) -> FastAPI:
    """挂载全部切片 + （若存在）前端构建产物。"""
    app = FastAPI(
        title="Hunter1",
        description="求职工作台 —— 本地优先，前后端完全分离",
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )

    def _runtime_llm() -> LLMProvider:
        """取当前模型配置构造客户端。

        运行时可变的配置（用户在配置页换 key / 换模型）必须**每次请求重读** ——
        启动时缓存一个客户端会让改配置要重启才生效。
        """
        settings = context.db.settings().get_llm()
        if settings is None or not settings.is_configured:
            raise RuntimeError("模型未配置：请先在「配置」页填好 base_url / 模型 / API Key")
        return context.llm_factory(settings)

    def _mount(router: APIRouter, prefix: str = API_PREFIX) -> None:
        app.include_router(router, prefix=prefix)

    runner = _build_runner(context)
    # 暴露给外部观察与测试注入（app.state 是 ASGI 约定的挂载点）：
    # 测试可以替换 runner 的抓取器工厂来走真链路；运维可查当前运行器状态。
    app.state.context = context
    app.state.runner = runner

    # ---- 切片路由（全部挂在 /api 下）----
    _mount(build_jobs_router(store=JobStore(context.db), clock=context.clock))
    _mount(build_crawl_router(runner=runner))
    _mount(build_applications_router(store=ApplicationStore(context.db), clock=context.clock))
    _mount(build_settings_router(store=SettingsStore(context.db), llm_factory=context.llm_factory))
    _mount(
        build_assistant_router(
            store=ConversationStore(context.db),
            llm_factory=_runtime_llm,
            tools=build_tools(jobs=context.db.jobs(), applications=context.db.applications()),
            history_limit=context.assistant_history_limit,
        )
    )
    if context.candidate_profile is not None:
        _mount(
            build_scoring_router(
                store=ScoreStore(context.db),
                llm_factory=_runtime_llm,
                profile=context.candidate_profile,
            )
        )

    # ---- 前端构建产物（交付形态）----
    _mount_frontend(app)
    return app


def _build_runner(context: AppContext) -> CrawlRunner:
    """构造抓取运行器（进程内单例，由 app.state 持有）。"""
    return CrawlRunner(
        crawler_factory=context.crawler_factory,
        jobs=context.db.jobs(),
        clock=context.clock,
    )


def _mount_frontend(app: FastAPI) -> None:
    """服务前端 SPA；产物不存在时给一条可读提示（开发态的预期情形）。

    SPA 回落规则：`/api/*` 之外的任何路径都交给 `index.html`（前端路由自己解析）。
    这必须在所有 API 路由**之后**注册，否则会把 API 路径也吞掉。
    """
    dist = frontend_dir()
    if dist is None:

        @app.get("/", include_in_schema=False)
        def no_frontend() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": "前端产物未构建",
                    "hint": "cd frontend && npm install && npm run build；"
                    "或开发期用 bash scripts/dev.sh 起 Vite（:5173）",
                    "api_docs": f"{API_PREFIX}/docs",
                },
                status_code=503,
            )

        return

    dist_root = dist.resolve()
    assets = dist_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    index = dist_root / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str = "") -> FileResponse:
        # 真实文件优先（favicon 等），其余交给 SPA。
        #
        # 边界校验：`path` 直取自 URL，可含 `..`（原始 socket / `curl --path-as-is` /
        # 浏览器发百分号编码 `%2e%2e%2f` 都能让它原样抵达）。解析后必须仍在 dist
        # 之内，否则这条**手工**拼路径会穿越到 dist 之外读到仓库文件。
        # （`/assets` 走 StaticFiles，Starlette 内部有保护；这条手工路径没有。）
        if path:
            candidate = (dist_root / path).resolve()
            if candidate.is_relative_to(dist_root) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)


__all__ = ["API_PREFIX", "AppContext", "create_app", "frontend_dir"]
