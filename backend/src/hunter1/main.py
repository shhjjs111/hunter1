"""组装根 —— 把切片、静态资源与运行配置接成一个 ASGI 应用。

**HTTP 应用的组装处认识所有切片**：切片自己不 import 彼此，也不知道
HTTP 之外的世界（见 AGENTS.md 的依赖方向表）。所有跨切片的接线都在这里。
（另有一个组装根 `cli.py`：它的 `crawl` / `update` 子命令直接驱动对应切片 ——
仓库里只有这两个文件允许横跨切片。）

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

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.convertors import Convertor, register_url_convertor

from hunter1.application.ports import Crawler, LLMProvider, ModelNotConfiguredError, TextFetcher
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


def _usable_dist(candidate: Path) -> Path | None:
    """这个目录能不能真的伺服 SPA —— 判据是 `index.html` 在不在。

    只判「目录存在」不够：半截构建 / `HUNTER1_FRONTEND_DIR` 指错 / 打包产物不完整时，
    目录在而 `index.html` 不在 —— SPA 回落的 `FileResponse` 会当场抛
    `RuntimeError: File at path ... does not exist`，于是**每一个非 API 路径都是 500**，
    而那条「前端产物未构建 + 构建命令」的 503 恰恰不会出现（它只在返回 None 时走）。
    用户看到的东西与真实原因（产物不完整）毫无关系。

    `index.html` 是 SPA 唯一必需的产物：`assets/` 缺失只是资源 404，少了它整站打不开。
    """
    return candidate if (candidate / "index.html").is_file() else None


def frontend_dir() -> Path | None:
    """前端构建产物所在目录；不可用返回 None（开发态的正常情形）。

    解析顺序：
    1. `HUNTER1_FRONTEND_DIR` 环境变量（测试与联调用）；
    2. 打包态：PyInstaller 解包目录下的 `hunter1/web_dist`；
    3. 源码态：仓库的 `frontend/dist`。

    三层都走 `_usable_dist`：「存在」不算数，「能伺服」才算。
    """
    override = os.environ.get("HUNTER1_FRONTEND_DIR")
    if override:
        return _usable_dist(Path(override))

    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return _usable_dist(bundled / "hunter1" / "web_dist")

    # main.py → hunter1 → src → backend → 仓库根
    return _usable_dist(Path(__file__).resolve().parents[3] / "frontend" / "dist")


@dataclass
class AppContext:
    """组装处依赖的一组能力（外部实现经此注入，便于测试替换）。"""

    db: Database
    fetcher: TextFetcher
    llm_factory: Callable[[LLMSettings], LLMProvider] = _default_llm_factory
    clock: Callable[[], datetime] = _now
    site_keys: list[str] | None = None
    assistant_history_limit: int = 20

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
    ) -> AppContext:
        """按本机默认配置装配（真实 SQLite + 真实 HTTP 抓取器）。"""
        database = Database(db_path)
        try:
            database.initialize()
        except BaseException:
            # 建库失败时别把引擎留在进程里：SQLite 会持有文件句柄、留下 -wal/-shm，
            # 而「删目录即卸载」的便携定位要求失败路径同样干净。
            database.dispose()
            raise
        if fetcher is None:
            from hunter1.platform.fetch.http import HttpFetcher

            # 生产装配在这里定「对站点多礼貌」：每主机 1 req/s + 遵守 robots.txt。
            # 平台层的默认值是「不额外等待」（测试要确定性、要快），策略留在组装根。
            fetcher = HttpFetcher(timeout=20, retries=2, min_interval=1.0, respect_robots=True)
        return cls(
            db=database,
            fetcher=fetcher,
            site_keys=site_keys,
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

        （客户端虽然每请求新建，但「这个端点拒绝过哪些结构化格式」的记忆是
        **进程级共享**的，按 (base_url, model) 键控 —— 见 `platform/llm` 里的
        `_rejected_modes_by_endpoint`。所以降级发现只付一次代价，不会每次调用
        都重吃一遍 400。）

        两种失败都归到 `ModelNotConfiguredError`（→ 409 + 指引），**不能 500**：

        - 没配过：`get_llm()` 返回 None；
        - 配坏了：`get_llm()` 抛 `ValueError`（区分「没配」与「配坏了」是它的刻意设计）。
          不接就会绕过异常处理器直穿成 500 —— 而「去配置页重填」是唯一出路，
          错误信息必须指向那里。
        """
        try:
            settings = context.db.settings().get_llm()
        except ValueError as exc:
            raise ModelNotConfiguredError(
                f"已保存的模型配置不可用，请到「配置」页重新填写：{exc}"
            ) from exc
        if settings is None or not settings.is_configured:
            raise ModelNotConfiguredError(
                "模型未配置：请先在「配置」页填好 base_url / 模型 / API Key"
            )
        return context.llm_factory(settings)

    def _mount(router: APIRouter, prefix: str = API_PREFIX) -> None:
        app.include_router(router, prefix=prefix)

    # 「模型未配置」是**初始状态**，不是服务端故障：统一映射为 409 + 可行动指引。
    # 注册在应用级而不是逐路由捕获：评分与助手（含流式）都经 `_runtime_llm`，
    # 一处覆盖全部；将来新增用模型的切片也自动继承同一语义。
    @app.exception_handler(ModelNotConfiguredError)
    async def _model_not_configured(
        _request: Request, exc: ModelNotConfiguredError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    runner = _build_runner(context)
    # 暴露给外部观察与测试注入（app.state 是 ASGI 约定的挂载点）：
    # 测试可以替换 runner 的抓取器工厂来走真链路；运维可查当前运行器状态。
    app.state.context = context
    app.state.runner = runner

    # ---- 切片路由（全部挂在 /api 下）----
    # jobs 的存储门面复用同一实例：applications 记录投递时要按 id 查岗位
    # （「投递」动作归 applications，岗位查询归 jobs —— 依赖方向白名单允许）。
    job_store = JobStore(context.db)
    _mount(build_jobs_router(store=job_store))
    _mount(build_crawl_router(runner=runner))
    _mount(
        build_applications_router(
            store=ApplicationStore(context.db),
            jobs=job_store,
            clock=context.clock,
        )
    )
    _mount(build_settings_router(store=SettingsStore(context.db), llm_factory=context.llm_factory))
    _mount(
        build_assistant_router(
            store=ConversationStore(context.db),
            llm_factory=_runtime_llm,
            tools=build_tools(jobs=context.db.jobs(), applications=context.db.applications()),
            history_limit=context.assistant_history_limit,
        )
    )
    # 评分：**始终挂载**。画像从库里读（用户在「配置」页写），未配置时端点
    # 返回 409 + 修复指引 —— 而不是干脆不挂载：那样前端照契约发出的 POST 会
    # 落进下面 SPA 回落的 GET 路由，收到 405（method not allowed），与真实原因无关。
    scoring_store = ScoreStore(context.db)
    _mount(
        build_scoring_router(
            store=scoring_store,
            llm_factory=_runtime_llm,
            profile_provider=scoring_store.load_profile,
            clock=context.clock,
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


class _SpaPathConvertor(Convertor[str]):
    """SPA 回落的路径参数：**排除 `api/` 前缀**。

    为什么需要它：`/{path:path}` 的 `.*` 会把**未注册**的 `/api/*` 也一并吞下 ——
    「后注册」只保护了**已注册**的路径。于是拼错端点的 GET 会拿到
    `200 text/html`（前端的 `response.json()` 直接炸，且看不出是路径写错），
    而 `POST /api/settings/test` 之外发 GET 也拿不到应有的 405。

    用带负向先行断言的转换器，让回落**根本匹配不到** `api/…`，Starlette 于是给出
    标准语义：路径不存在 → 404 JSON（FastAPI 的 HTTPException 处理器），
    路径存在但方法不对 → 405 + Allow 头。
    """

    regex = r"(?!api(?:/|$)).*"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("spa_path", _SpaPathConvertor())


def _static_file_within(dist_root: Path, path: str) -> Path | None:
    """把 URL 路径解析成 `dist_root` 内的真实文件；越界或非法路径返回 None。

    `path` 直取自 URL，可含 `..`（原始 socket / `curl --path-as-is` / 浏览器发
    百分号编码 `%2e%2e%2f` 都能让它原样抵达）。解析后必须仍在 dist 之内，否则
    这条**手工**拼路径会穿越到 dist 之外读到仓库文件。（`/assets` 走 StaticFiles，
    Starlette 内部有保护；这条手工路径没有。）

    含 NUL 等非法字符的路径（`/a%00b`）会让 `Path.resolve()` 抛
    `ValueError: embedded null character in path` —— 未捕获的异常，线上即 500。
    这类路径不可能对应任何真实文件，直接当作「没有这个文件」。
    """
    if "\x00" in path:
        return None
    try:
        candidate = (dist_root / path).resolve()
    except (OSError, ValueError):
        return None
    if candidate.is_relative_to(dist_root) and candidate.is_file():
        return candidate
    return None


def _frontend_missing() -> JSONResponse:
    """前端产物不可用时的可读回应（503 + 可行动的下一步）。"""
    return JSONResponse(
        {
            "detail": "前端产物未构建",
            "hint": "cd frontend && npm install && npm run build；"
            "或开发期用 bash scripts/dev.sh 起 Vite（:5173）",
            "api_docs": f"{API_PREFIX}/docs",
        },
        status_code=503,
    )


def _mount_frontend(app: FastAPI) -> None:
    """服务前端 SPA；产物不存在时给一条可读提示（开发态的预期情形）。

    SPA 回落规则：`/api/*` **之外**的任何路径都交给 `index.html`（前端路由自己
    解析）。这必须在所有 API 路由**之后**注册，否则会把 API 路径也吞掉；
    而「未注册的 `/api/*`」由 `_SpaPathConvertor` 挡在匹配之外（见其 docstring）。
    """
    dist = frontend_dir()
    if dist is None:

        @app.get("/", include_in_schema=False)
        def no_frontend() -> JSONResponse:
            return _frontend_missing()

        return

    dist_root = dist.resolve()
    assets = dist_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    index = dist_root / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/{path:spa_path}", include_in_schema=False)
    def spa(path: str = "") -> Response:
        # 真实文件优先（favicon、robots.txt、预渲染页…），其余交给 SPA。
        if path:
            found = _static_file_within(dist_root, path)
            if found is not None:
                return FileResponse(found)
        if not index.is_file():
            # 启动时 `frontend_dir()` 判过它在 —— 走到这里说明产物**启动后被换掉**了
            # （部署脚本正在覆盖目录）。照实回可读 503，别让 `FileResponse` 抛
            # `RuntimeError` 变成 500：那是个与原因毫无关系的报错。
            return _frontend_missing()
        return FileResponse(index)


__all__ = ["API_PREFIX", "AppContext", "create_app", "frontend_dir"]
