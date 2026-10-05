"""本地 Web UI —— FastAPI 应用工厂。

界面层只做「翻译」：把 HTTP 输入转成应用层用例调用，把结果渲染成 HTML。
业务规则不在这里（见 `application/` 与 `domain/`）。

设计选择：
- **服务端渲染 + 原生表单**，不引入前端构建链（规划 §3.3）；
- **应用工厂 `create_app(context)`**：所有外部能力经 `AppContext` 注入，
  因此路由测试可以完全离线（假模型 + 假抓取器 + 真 SQLite 临时库）；
- 写操作（记录投递、改阶段、跑抓取）走 **POST + 303 重定向**，
  刷新页面不会重复提交。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from hunter1.application.applications import change_stage, new_application
from hunter1.domain.assistant import Message, Role
from hunter1.domain.llm import TextDelta
from hunter1.domain.models import ApplicationStage, Job
from hunter1.domain.settings import LLMSettings
from hunter1.slices.applications.router import build_router as build_applications_router
from hunter1.slices.applications.store import ApplicationStore
from hunter1.slices.assistant.job_tools import build_tools
from hunter1.slices.assistant.router import build_router as build_assistant_router
from hunter1.slices.assistant.service import (
    ToolFinished,
    ToolStarted,
    TurnDone,
    run_turn,
    run_turn_stream,
)
from hunter1.slices.assistant.store import ConversationStore
from hunter1.slices.crawl.router import build_router as build_crawl_router
from hunter1.slices.crawl.runner import CrawlRunner
from hunter1.slices.jobs.router import build_router as build_jobs_router
from hunter1.slices.jobs.store import JobStore
from hunter1.slices.scoring.router import build_router as build_scoring_router
from hunter1.slices.scoring.store import ScoreStore
from hunter1.web.context import AppContext

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

STAGE_LABELS: dict[str, str] = {
    "applied": "已投递",
    "written_test": "笔试",
    "interview": "面试",
    "offer": "Offer",
    "rejected": "已拒",
    "withdrawn": "已放弃",
}

_FALLBACK_REPLY = "（模型没有返回内容，请重试或换一个模型。）"

# 页码上限：只设下界时 page=999999 会变成天量 OFFSET（SQLite 要扫描并丢弃
# 前面所有行才能定位）。真实使用没人会翻到第 10000 页，但「无限」由此变「有界」。
_MAX_PAGE = 10_000


def create_app(context: AppContext) -> FastAPI:
    app = FastAPI(title="Hunter1", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    runner = CrawlRunner(
        crawler_factory=context.crawler_factory,
        jobs=context.db.jobs(),
        clock=context.clock,
    )
    app.state.context = context
    app.state.runner = runner

    # ---- 切片 API（JSON / SSE）—— 过渡期与旧 SSR 页面共存于同一 app ----
    # 组装处注入依赖：切片不 import web（依赖方向 web → slices）。
    def _slice_llm() -> object:
        """取当前模型配置构造客户端。

        运行时可变的配置（用户在设置页换 key / 换模型）必须**每次请求重读** ——
        启动时缓存一个客户端会让改配置要重启才生效。
        """
        settings = context.db.settings().get_llm()
        if settings is None or not settings.is_configured:
            raise RuntimeError("模型未配置：请先在「配置」页填好 base_url / 模型 / API Key")
        return context.llm_factory(settings)

    app.include_router(
        build_jobs_router(store=JobStore(context.db), clock=context.clock),
        prefix="/api",
    )
    app.include_router(
        build_crawl_router(runner=runner),
        prefix="/api",
    )
    app.include_router(
        build_applications_router(store=ApplicationStore(context.db), clock=context.clock),
        prefix="/api",
    )
    app.include_router(
        build_assistant_router(
            store=ConversationStore(context.db),
            llm_factory=_slice_llm,  # type: ignore[arg-type]
            tools=build_tools(jobs=context.db.jobs(), applications=context.db.applications()),
            history_limit=context.assistant_history_limit,
        ),
        prefix="/api",
    )
    # 画像未配置时不挂评分端点：「没接线」表现为「端点不存在」，
    # 而不是「端点存在但总是报错」（与 job_tools 里 applications 工具同一原则）。
    if context.candidate_profile is not None:
        app.include_router(
            build_scoring_router(
                store=ScoreStore(context.db),
                llm_factory=_slice_llm,  # type: ignore[arg-type]
                profile=context.candidate_profile,
            ),
            prefix="/api",
        )

    def render(request: Request, name: str, **extra: object) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            name,
            {
                "stage_labels": STAGE_LABELS,
                "stages": list(ApplicationStage),
                "sites": context.site_keys,
                **extra,
            },
        )

    # ---- 岗位库 ----

    @app.get("/", response_class=HTMLResponse)
    def jobs_page(request: Request, q: str = "", page: int = 1) -> HTMLResponse:
        size = max(1, context.page_size)
        page = min(max(1, page), _MAX_PAGE)
        repo = context.db.jobs()
        keyword = q.strip()
        offset = (page - 1) * size

        jobs: list[Job] = (
            repo.search(keyword=keyword, limit=size, offset=offset)
            if keyword
            else repo.list(limit=size, offset=offset)
        )
        total = repo.count(keyword=keyword) if keyword else repo.count()
        return render(
            request,
            "jobs.html",
            active="jobs",
            jobs=jobs,
            q=keyword,
            page=page,
            page_size=size,
            total=total,
            has_next=offset + len(jobs) < total,
        )

    @app.post("/jobs/{job_id}/apply")
    def job_apply(job_id: str) -> RedirectResponse:
        job = context.db.jobs().get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"岗位不存在：{job_id}")
        application = new_application(job=job, now=context.clock())
        context.db.applications().upsert(application)
        return RedirectResponse("/applications?recorded=1", status_code=303)

    # ---- 投递记录 ----

    @app.get("/applications", response_class=HTMLResponse)
    def applications_page(request: Request, recorded: str = "") -> HTMLResponse:
        return render(
            request,
            "applications.html",
            active="applications",
            applications=context.db.applications().list(limit=200),
            recorded=bool(recorded),
        )

    @app.post("/applications/{application_id}/stage")
    def application_stage(
        application_id: str, stage: str = Form(...), note: str = Form("")
    ) -> RedirectResponse:
        repo = context.db.applications()
        application = repo.get(application_id)
        if application is None:
            raise HTTPException(status_code=404, detail=f"投递记录不存在：{application_id}")
        try:
            new_stage = ApplicationStage(stage)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"未知阶段：{stage}") from None
        repo.upsert(
            change_stage(
                application,
                stage=new_stage,
                now=context.clock(),
                note=note.strip() or None,
            )
        )
        return RedirectResponse("/applications", status_code=303)

    @app.post("/applications/{application_id}/delete")
    def application_delete(application_id: str) -> RedirectResponse:
        context.db.applications().delete(application_id)
        return RedirectResponse("/applications", status_code=303)

    # ---- 配置 ----

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, saved: str = "") -> HTMLResponse:
        return render(
            request,
            "settings.html",
            active="settings",
            form=_settings_form(context.db.settings().get_llm()),
            presets=_presets(),
            saved=bool(saved),
            test_result=None,
        )

    @app.post("/settings", response_class=HTMLResponse)
    def settings_save(
        request: Request,
        base_url: str = Form(...),
        model: str = Form(...),
        api_key: str = Form(""),
        temperature: str = Form(""),
        max_tokens: str = Form(""),
    ) -> Response:
        repo = context.db.settings()
        existing = repo.get_llm()
        # 界面只回显掩码，所以空 key 表示「不改」而不是「清空」——
        # 否则用户每改一次模型都会把自己的 key 抹掉。
        key = api_key.strip() or (existing.api_key if existing else "")
        draft = {
            "base_url": base_url,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            candidate = LLMSettings(
                base_url=base_url,
                model=model,
                api_key=key,
                temperature=_float_or_none(temperature),
                max_tokens=_int_or_none(max_tokens),
            )
        except ValidationError as exc:
            # 就地把错误回显出来，并保留用户刚填的内容 ——
            # 直接抛出去只会给用户一个 500 页面，等于没告诉他哪儿错了。
            return render(
                request,
                "settings.html",
                active="settings",
                form=_settings_form(existing, draft=draft),
                presets=_presets(),
                saved=False,
                test_result={"ok": "0", "message": _readable(exc)},
            )
        repo.save_llm(candidate)
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.post("/settings/test", response_class=HTMLResponse)
    def settings_test(request: Request) -> HTMLResponse:
        settings = context.db.settings().get_llm()
        if settings is None or not settings.is_configured:
            result: dict[str, str] = {
                "ok": "0",
                "message": "配置不完整：base_url / model / api_key 都要填。",
            }
        else:
            result = _probe(context, settings)
        return render(
            request,
            "settings.html",
            active="settings",
            form=_settings_form(settings),
            presets=_presets(),
            saved=False,
            test_result=result,
        )

    # ---- 助手 ----

    @app.get("/assistant", response_class=HTMLResponse)
    def assistant_page(request: Request, cid: str = "", error: str = "") -> HTMLResponse:
        repo = context.db.conversations()
        conversations = repo.list(limit=50)
        current = repo.get(cid) if cid else (conversations[0] if conversations else None)
        settings = context.db.settings().get_llm()
        return render(
            request,
            "assistant.html",
            active="assistant",
            conversations=conversations,
            current=current,
            messages=repo.messages(current.id) if current is not None else [],
            configured=settings is not None and settings.is_configured,
            error=error,
        )

    @app.post("/assistant")
    def assistant_send(
        message: str = Form(...), conversation_id: str = Form("")
    ) -> RedirectResponse:
        text = message.strip()
        if not text:
            return RedirectResponse("/assistant", status_code=303)

        settings = context.db.settings().get_llm()
        if settings is None or not settings.is_configured:
            return RedirectResponse(
                f"/assistant?error={quote('请先在「配置」页填好 API Key 与模型')}",
                status_code=303,
            )

        repo = context.db.conversations()
        conversation = repo.get(conversation_id) if conversation_id else None
        history = (
            repo.messages(conversation.id, limit=context.assistant_history_limit)
            if conversation is not None
            else []
        )
        user_message = Message(role=Role.USER, content=text)
        registry = build_tools(jobs=context.db.jobs(), applications=context.db.applications())

        try:
            turn = run_turn(
                llm=context.llm_factory(settings),
                registry=registry,
                messages=[*history, user_message],
            )
        except Exception as exc:
            reason = quote(_brief(f"{type(exc).__name__}: {exc}"))
            target = f"/assistant?error={reason}"
            if conversation is not None:
                target += f"&cid={conversation.id}"
            return RedirectResponse(target, status_code=303)

        # 成功才建档落库：失败的尝试不留空会话，也不留半截对话
        # （免得下次把失败的用户消息当上下文再问一遍）。
        if conversation is None:
            conversation = repo.create(title=text[:40])
        repo.append(conversation.id, user_message)
        repo.append(
            conversation.id,
            Message(
                role=Role.ASSISTANT,
                content=turn.reply.strip() or _FALLBACK_REPLY,
            ),
        )
        return RedirectResponse(f"/assistant?cid={conversation.id}", status_code=303)

    @app.post("/assistant/stream")
    def assistant_stream(
        message: str = Form(...), conversation_id: str = Form("")
    ) -> StreamingResponse:
        """流式回合：以 `text/event-stream` 边生成边推。

        与 `/assistant` 的差别只在交付方式 —— 内容逐段到达，工具执行前后各有
        一个事件。**失败也走事件流**（一条 `error` 事件）：响应一旦开始就没法
        再重定向，这是流式端点最容易漏掉的一条。
        """
        text = message.strip()
        settings = context.db.settings().get_llm()

        if not text:
            return _event_stream([_error_event("请输入内容后再发送。")])
        if settings is None or not settings.is_configured:
            return _event_stream([_error_event("请先在「配置」页填好 API Key 与模型。")])

        return StreamingResponse(
            _stream_turn(context, text=text, conversation_id=conversation_id, settings=settings),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    # ---- 抓取 ----

    @app.get("/crawl", response_class=HTMLResponse)
    def crawl_page(request: Request, started: str = "", refused: str = "") -> HTMLResponse:
        return render(
            request,
            "crawl.html",
            active="crawl",
            snapshot=runner.snapshot(),
            started=bool(started),
            refused=bool(refused),
        )

    @app.post("/crawl")
    def crawl_start() -> RedirectResponse:
        if runner.start():
            return RedirectResponse("/crawl?started=1", status_code=303)
        return RedirectResponse("/crawl?refused=1", status_code=303)

    @app.get("/api/crawl/status")
    def crawl_status() -> JSONResponse:
        return JSONResponse(runner.snapshot().as_dict())

    return app


def _settings_form(
    settings: LLMSettings | None, *, draft: dict[str, str] | None = None
) -> dict[str, object]:
    """配置页要渲染的表单值。

    有 `draft` 时优先用它 —— 校验失败要把用户刚填的东西原样还给他，
    否则他得从头再敲一遍。
    """

    def pick(key: str, fallback: object) -> object:
        if draft is not None:
            return draft[key]
        return fallback

    return {
        "base_url": pick("base_url", settings.base_url if settings else ""),
        "model": pick("model", settings.model if settings else ""),
        "temperature": pick(
            "temperature",
            settings.temperature if settings and settings.temperature is not None else "",
        ),
        "max_tokens": pick(
            "max_tokens",
            settings.max_tokens if settings and settings.max_tokens is not None else "",
        ),
        "key_hint": settings.masked_key() if settings else "sk-...",
        "configured": bool(settings and settings.is_configured),
    }


def _brief(message: str, limit: int = 300) -> str:
    """把错误消息压短：它要进查询串，原样塞进去会撑出一条超长 URL。"""
    text = " ".join((message or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---- SSE（助手流式）----

# `X-Accel-Buffering: no` 是给反向代理看的：否则 nginx 一类会把分片攒起来
# 一次性下发，「逐字输出」就名存实亡。
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# 工具结果可能很长（搜出几十条岗位），但前端只需要一个摘要。
_TOOL_PREVIEW_LIMIT = 1000


def _sse(event: dict[str, object]) -> str:
    """一条 SSE 事件。用 `\\n\\n` 收尾是协议要求的分隔。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _error_event(message: str) -> dict[str, object]:
    return {"type": "error", "message": message}


def _event_stream(events: list[dict[str, object]]) -> StreamingResponse:
    """把「已经知道结果」的事件列表包成流（用于开流前的校验失败）。"""

    def generate() -> Iterator[str]:
        for event in events:
            yield _sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _stream_turn(
    context: AppContext, *, text: str, conversation_id: str, settings: LLMSettings
) -> Iterator[str]:
    """跑一轮流式对话并把每个事件翻成 SSE。

    落库时机与 `/assistant` 保持一致：**整轮跑完才写**。失败（模型报错、
    中止）不留空会话，也不留半截对话 —— 否则下次会把失败的那句话当成上下文
    再问一遍。
    """
    repo = context.db.conversations()

    try:
        conversation = repo.get(conversation_id) if conversation_id else None
        history = (
            repo.messages(conversation.id, limit=context.assistant_history_limit)
            if conversation is not None
            else []
        )
        user_message = Message(role=Role.USER, content=text)
        registry = build_tools(jobs=context.db.jobs(), applications=context.db.applications())

        final: TurnDone | None = None
        for event in run_turn_stream(
            llm=context.llm_factory(settings),
            registry=registry,
            messages=[*history, user_message],
        ):
            if isinstance(event, TextDelta):
                yield _sse({"type": "text", "text": event.text})
            elif isinstance(event, ToolStarted):
                yield _sse({"type": "tool_start", "name": event.name, "arguments": event.arguments})
            elif isinstance(event, ToolFinished):
                yield _sse(
                    {
                        "type": "tool_end",
                        "name": event.name,
                        "ok": event.ok,
                        "content": event.content[:_TOOL_PREVIEW_LIMIT],
                        "error": event.error,
                    }
                )
            else:
                final = event

        if conversation is None:
            conversation = repo.create(title=text[:40])
        repo.append(conversation.id, user_message)
        repo.append(
            conversation.id,
            Message(role=Role.ASSISTANT, content=final.reply.strip() or _FALLBACK_REPLY)
            if final is not None and final.reply.strip()
            else Message(role=Role.ASSISTANT, content=_FALLBACK_REPLY),
        )

        yield _sse(
            {
                "type": "done",
                "conversation_id": conversation.id,
                "reply": final.reply if final is not None else "",
                "truncated": bool(final and final.truncated),
                "degraded": bool(final and final.degraded),
            }
        )
    except Exception as exc:
        yield _sse(_error_event(_brief(f"{type(exc).__name__}: {exc}")))


def _readable(exc: ValidationError) -> str:
    """把 pydantic 的报错压成一句人能读的话。"""
    errors = exc.errors()
    if not errors:
        return str(exc)
    message = str(errors[0].get("msg", exc))
    return message.removeprefix("Value error, ")


def _probe(context: AppContext, settings: LLMSettings) -> dict[str, str]:
    """真发一次最小请求，判断这套配置能不能用。"""
    try:
        llm = context.llm_factory(settings)
        response = llm.complete(
            system_prompt="你是连通性测试助手。",
            user_prompt="只回复两个字：可用",
            max_tokens=16,
        )
    except Exception as exc:
        return {"ok": "0", "message": f"{type(exc).__name__}: {exc}"}
    model = response.model or settings.model
    return {"ok": "1", "message": f"连接成功，模型 {model} 已应答。"}


def _presets() -> dict[str, dict[str, str]]:
    from hunter1.platform.llm import PROVIDER_PRESETS

    return {
        name: {"base_url": preset.base_url, "model": preset.default_model}
        for name, preset in PROVIDER_PRESETS.items()
    }


def _float_or_none(raw: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int_or_none(raw: str) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


__all__ = ["STAGE_LABELS", "create_app"]
