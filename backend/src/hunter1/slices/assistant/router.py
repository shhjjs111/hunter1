"""assistant 切片的 HTTP 面 —— 会话列表 + SSE 流式对话。

组装处调用 `build_router(store=..., llm_factory=..., tools=...)` 注入依赖；
切片不 import web。**工具集也走注入**（不在切片里摸全局状态）——
工具依赖的仓储由组装处决定，切片只负责调用。

SSE 的两条硬约定（沿用旧界面的实现教训）：

1. **失败也走事件流**：响应一旦开始就没法再重定向，所以模型报错必须作为
   一条 `error` 事件交出去，而不是让连接悄悄断掉；
2. **整轮跑完才落库**：失败的尝试不留空会话、不留半截对话 —— 否则下次会把
   失败的那句话当成上下文再问一遍。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from hunter1.application.ports import LLMProvider
from hunter1.domain.assistant import Message, Role
from hunter1.domain.llm import TextDelta
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


class StreamRequest(BaseModel):
    """对话请求体（流式与一次性共用）。"""

    message: str = Field(min_length=1)
    conversation_id: str = ""


class TurnResponse(BaseModel):
    """一次性对话的结果。"""

    conversation_id: str
    reply: str
    iterations: int
    truncated: bool


class ConversationSummary(BaseModel):
    """会话列表项。"""

    id: str
    title: str
    updated_at: str


class ConversationMessageView(BaseModel):
    """会话里的一条消息。"""

    role: str
    content: str


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
    """成功才落库：新建会话（如果还没有）+ 追加用户消息与助手回复。"""
    conversation = store.get(conversation_id) if conversation_id else None
    if conversation is None:
        conversation = store.create(title=title[:40])
    store.append(conversation.id, user_message)
    store.append(
        conversation.id,
        Message(role=Role.ASSISTANT, content=reply.strip() or FALLBACK_REPLY),
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
            ConversationSummary(
                id=item.id, title=item.title, updated_at=item.updated_at.isoformat()
            )
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
        try:
            result: AssistantResult = run_turn(
                llm=llm_factory(), registry=tools, messages=[*history, user_message]
            )
        except Exception as exc:
            # 不落库：失败的尝试不留会话（免得下次把失败那句当上下文）
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

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
        return StreamingResponse(
            _stream_turn(
                store=store,
                llm=llm_factory(),
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


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "FALLBACK_REPLY",
    "SSE_HEADERS",
    "TOOL_PREVIEW_LIMIT",
    "StreamRequest",
    "build_router",
]
