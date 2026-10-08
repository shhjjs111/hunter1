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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from hunter1.application.ports import LLMProvider, ModelNotConfiguredError
from hunter1.domain.assistant import Message, Role
from hunter1.domain.llm import LLMError, TextDelta
from hunter1.slices.assistant.schemas import (
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

FALLBACK_REPLY = "（模型没有返回内容，请重试或换一个模型。）"


def _sse(event: dict[str, object]) -> str:
    """一条 SSE 事件。用 `\\n\\n` 收尾是协议要求的分隔。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


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
            Message(role=Role.ASSISTANT, content=reply.strip() or FALLBACK_REPLY),
        ],
    )
    return conversation.id


def _static_stream(events: list[dict[str, object]]) -> StreamingResponse:
    """把「已经知道结果」的事件列表包成流（开流前的校验失败用）。"""

    def generate() -> Iterator[str]:
        for event in events:
            yield _sse(event)

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


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
        "/assistant/conversations", response_model=list[ConversationSummary], summary="会话列表"
    )
    def list_conversations() -> list[ConversationSummary]:
        return [
            ConversationSummary(id=item.id, title=item.title, updated_at=item.updated_at)
            for item in store.list(limit=50)
        ]

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
                "reply": final.reply if final is not None else "",
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
