"""assistant 切片的 HTTP 面 —— 会话列表 + SSE 流式对话。

组装处调用 `build_router(store=..., llm_factory=..., tools=...)` 注入依赖；
切片不 import web。**工具集也走注入**（不在切片里摸全局状态）——
工具依赖的仓储由组装处决定，切片只负责调用。

SSE 的两条硬约定（沿用旧界面的实现教训）：

1. **失败也走事件流**：响应一旦开始就没法再重定向，所以模型报错必须作为
   一条 `error` 事件交出去，而不是让连接悄悄断掉；
2. **整轮跑完才落库**：失败的尝试不留空会话、不留半截对话 —— 否则下次会把
   失败的那句话当成上下文再问一遍。

错误语义（与 scoring 切片对齐）：

| 状态 | 含义 |
|---|---|
| 409 | 模型未配置 / 配置不可用 —— 请求没毛病，服务端状态未就绪 |
| 422 | 请求体不合法（空消息）或**上游模型失败**（`LLMError`） |
| 500 | 非契约异常：`LLMProvider` 抛 `LLMError` 之外的异常说明是实现 bug，应当响亮地失败 |

（原先 /turn 用 502 表示上游失败、且用 `except Exception` 一把兜住 —— 同一个上游
故障在两处给两种码，实现 bug 还会被伪装成「上游故障」。）
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from hunter1.application.ports import LLMProvider, ModelNotConfiguredError
from hunter1.domain.assistant import Message, Role
from hunter1.domain.llm import LLMError, TextDelta
from hunter1.slices.assistant.schemas import (
    ConversationListResponse,
    ConversationMessageView,
    ConversationSummary,
    StreamRequest,
    TurnResponse,
)
from hunter1.slices.assistant.service import (
    AssistantResult,
    ToolFinished,
    ToolStarted,
    TurnDone,
    run_turn,
    run_turn_stream,
)
from hunter1.slices.assistant.store import ConversationStore
from hunter1.slices.assistant.tools import ToolRegistry

# `X-Accel-Buffering: no` 是给反向代理看的：否则 nginx 一类会把分片攒起来
# 一次性下发，「逐字输出」就名存实亡。
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# 工具结果可能很长（搜出几十条岗位），但前端只需要一个摘要。
TOOL_PREVIEW_LIMIT = 1000

# 历史上下文的默认截断条数（对话越长越该保留近期）。
DEFAULT_HISTORY_LIMIT = 20

#: 一页最多给多少个会话 —— 也就是原 `LIST_LIMIT` 的角色，且**就是默认页大小**：
#: 首页与改动前逐条一致（≤50 个会话时体验零变化），第 51 个起则从「无法触达」
#: 变成「翻一页就到」。没有「一次取全部」的口子，上限就是它。
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = MAX_PAGE_SIZE
#: 页码上界，与 jobs 的 `MAX_PAGE` 同一把尺（值也一样）：只设下界时 page=999999
#: 会变成天量 OFFSET —— SQLite 得扫描并丢弃前面所有行才能定位。越界由 422 拒绝，
#: **不静默钳制**（悄悄改成第 10000 页会让调用方从响应里看不出入参被改过，而
#: OpenAPI 的 description 给不了这个保证）。
MAX_PAGE = 10_000

FALLBACK_REPLY = "（模型没有返回内容，请重试或换一个模型。）"


def _sse(event: dict[str, object]) -> str:
    """一条 SSE 事件。用 `\\n\\n` 收尾是协议要求的分隔。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _stored_reply(reply: str) -> str:
    """助手回复的**规范形式**：空白一律换成兜底文案。

    落库与 `done` 事件必须走**同一个**入口。分头各写一遍就会出现「库里存了兜底、
    事件里报的是空字符串」—— 界面显示这条回答缺了，重新打开会话却又看得见内容，
    两个面各自都「对」，合起来是矛盾。
    """
    return reply.strip() or FALLBACK_REPLY


def _persist(
    store: ConversationStore,
    *,
    conversation_id: str,
    title: str,
    user_message: Message,
    reply: str,
) -> str:
    """成功才落库：新建会话（如果还没有）+ 追加用户消息与助手回复。

    两条消息经 `append_many` **同事务**写入 —— 否则第二条失败会留下「有问无答」
    的半截对话，下次把失败那句当上下文再问一遍。
    """
    conversation = store.get(conversation_id) if conversation_id else None
    if conversation is None:
        conversation = store.create(title=title[:40])
    store.append_many(
        conversation.id,
        [
            user_message,
            Message(role=Role.ASSISTANT, content=_stored_reply(reply)),
        ],
    )
    return conversation.id


def _static_stream(events: list[dict[str, object]]) -> StreamingResponse:
    """把「已经知道结果」的事件列表包成流（开流前的校验失败用）。"""

    def generate() -> Iterator[str]:
        for event in events:
            yield _sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


def _history_for(
    store: ConversationStore, conversation_id: str, history_limit: int
) -> list[Message]:
    """取会话历史；**传了 conversation_id 却查不到**时 404。

    原先两个**写**端点对未知 id 静默新建会话（history 为空），而同切片的**读**端点
    对同一资源返回 404 —— 同一资源「不存在」在读写两个面上语义相反。后果：拼错或
    已删除的 id 永不暴露，用户以为在续一段对话，实际上下文已丢且无从察觉。
    这里与读端点对齐（`conversation_messages` 的 404 是同一语义）。
    """
    if not conversation_id:
        return []
    conversation = store.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{conversation_id}")
    return store.messages(conversation.id, limit=history_limit)


def build_router(
    *,
    store: ConversationStore,
    llm_factory: Callable[[], LLMProvider],
    tools: ToolRegistry,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> APIRouter:
    """构造 assistant 的 APIRouter（依赖由组装处注入）。"""
    router = APIRouter()

    @router.get(
        "/assistant/conversations",
        response_model=ConversationListResponse,
        summary="会话列表（分页）",
    )
    def list_conversations(
        page: int = Query(1, ge=1, le=MAX_PAGE, description="页码（1 起）"),
        page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> ConversationListResponse:
        total = store.count()
        offset = (page - 1) * page_size
        items = [
            ConversationSummary(id=item.id, title=item.title, updated_at=item.updated_at)
            for item in store.list(limit=page_size, offset=offset)
        ]
        # `has_next` 用**实际取回条数**判（与 jobs 同一判据）：比 `page*page_size < total`
        # 稳 —— 末页恰好取满、或 total 在两次查询之间变了，都不会算错。
        return ConversationListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            has_next=offset + len(items) < total,
        )

    @router.get(
        "/assistant/conversations/{conversation_id}",
        response_model=list[ConversationMessageView],
        summary="会话消息",
    )
    def conversation_messages(conversation_id: str) -> list[ConversationMessageView]:
        if store.get(conversation_id) is None:
            raise HTTPException(status_code=404, detail=f"会话不存在：{conversation_id}")
        return [
            ConversationMessageView(role=str(message.role), content=message.content)
            for message in store.messages(conversation_id)
        ]

    # ---- 一次性对话（无流式能力的调用方 / 脚本用）----

    @router.post("/assistant/turn", response_model=TurnResponse, summary="跑一轮对话（一次性返回）")
    def turn(body: StreamRequest) -> TurnResponse:
        text = body.message.strip()
        if not text:
            raise HTTPException(status_code=422, detail="请输入内容后再发送。")
        conversation = store.get(body.conversation_id) if body.conversation_id else None
        if body.conversation_id and conversation is None:
            # 与读端点同一语义（见 `_history_for` 的说明）。
            raise HTTPException(status_code=404, detail=f"会话不存在：{body.conversation_id}")
        history = store.messages(conversation.id, limit=history_limit) if conversation else []
        user_message = Message(role=Role.USER, content=text)
        llm = llm_factory()
        try:
            result: AssistantResult = run_turn(
                llm=llm, registry=tools, messages=[*history, user_message]
            )
        except ModelNotConfiguredError:
            # 「模型未配置」不是**上游故障**（根本没发出请求）；交给应用级处理器
            # 映射为 409 + 指引（与 scoring 切片同一语义）。
            raise
        except LLMError as exc:
            # 上游模型失败（端口契约 `LLMProvider` 约定失败抛 `LLMError`）：
            # 与 scoring 切片**同一状态码** 422 + 可读原因 —— 同一个上游故障在
            # 两处给两种码，前端与文档就都得各记一套（原先这里是 502）。
            #
            # 刻意**不**捕宽泛的 `Exception`：抛别的说明是实现 bug（TypeError…），
            # 那种情况应当响亮地 500，而不是被伪装成「上游故障」—— 伪装会让人
            # 拿着错误的线索去查网络与厂商。
            # 不落库：失败的尝试不留会话（免得下次把失败那句当上下文）。
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            # 客户端每请求新建 —— 用完释放，别把连接池攒在进程里
            llm.close()

        conversation_id = _persist(
            store,
            conversation_id=body.conversation_id,
            title=text,
            user_message=user_message,
            reply=result.reply,
        )
        return TurnResponse(
            conversation_id=conversation_id,
            reply=result.reply.strip() or FALLBACK_REPLY,
            iterations=result.iterations,
            truncated=result.truncated,
        )

    # ---- 流式对话（SSE）----

    @router.post("/assistant/stream", summary="流式对话（SSE）")
    def stream(body: StreamRequest) -> StreamingResponse:
        text = body.message.strip()
        if not text:
            return _static_stream([{"type": "error", "message": "请输入内容后再发送。"}])
        # 未知会话在**开流之前**就 404：此刻响应尚未开始，raise 会走 FastAPI 的
        # 异常处理器，前端拿到的是一条标准的 404 JSON（与一次性端点、读端点一致）。
        # 放到 generator 里就晚了 —— 那时响应已开始，只能作为一条 SSE error 事件发出，
        # 前端得为同一契约写两套判断。
        if body.conversation_id:
            _history_for(store, body.conversation_id, history_limit)
        llm = llm_factory()
        # 所有权交给 generator：真正的消费发生在响应体被读取时（端点这时早已返回），
        # 所以释放只能由 `_stream_turn` 的 finally 负责 —— 客户端中途断开时 Starlette
        # 会 close 这个 generator，finally 同样会跑到。
        return StreamingResponse(
            _stream_turn(
                store=store,
                llm=llm,
                tools=tools,
                text=text,
                conversation_id=body.conversation_id,
                history_limit=history_limit,
            ),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return router


def _stream_turn(
    *,
    store: ConversationStore,
    llm: LLMProvider,
    tools: ToolRegistry,
    text: str,
    conversation_id: str,
    history_limit: int,
) -> Iterator[str]:
    """跑一轮流式对话并把每个事件翻成 SSE。

    落库时机与一次性端点一致：**整轮跑完才写**。
    """
    try:
        conversation = store.get(conversation_id) if conversation_id else None
        history = store.messages(conversation.id, limit=history_limit) if conversation else []
        user_message = Message(role=Role.USER, content=text)

        final: TurnDone | None = None
        for event in run_turn_stream(llm=llm, registry=tools, messages=[*history, user_message]):
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
                        "content": event.content[:TOOL_PREVIEW_LIMIT],
                        "error": event.error,
                    }
                )
            else:
                final = event

        conversation_id_out = _persist(
            store,
            conversation_id=conversation_id,
            title=text,
            user_message=user_message,
            reply=(final.reply if final is not None else ""),
        )
        yield _sse(
            {
                "type": "done",
                "conversation_id": conversation_id_out,
                "reply": _stored_reply(final.reply if final is not None else ""),
                "truncated": bool(final and final.truncated),
                "degraded": bool(final and final.degraded),
            }
        )
    except Exception as exc:
        # 响应已经开始，没法再重定向 —— 失败必须作为事件交出去
        yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        # 本 generator 拥有这个客户端（见 `stream` 端点的注释）：正常结束、出错、
        # 或客户端断开（GeneratorExit）—— 三条路径都要把连接池释放掉。
        llm.close()


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "FALLBACK_REPLY",
    "SSE_HEADERS",
    "TOOL_PREVIEW_LIMIT",
    "StreamRequest",
    "build_router",
]
