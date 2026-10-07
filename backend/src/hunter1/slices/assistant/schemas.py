"""assistant 切片的 API 模型 —— 跨端契约的**唯一事实来源**。

这些 Pydantic 模型经 OpenAPI 快照（`contracts/openapi.json`）流向
前端类型（`frontend/src/shared/api/schema.d.ts`）。改这里 = 改契约：
改完跑 `bash scripts/contracts.sh` 重新导出并提交快照。

（原先这些模型内联在 `router.py` 里 —— 本仓库其余切片都按 AGENTS.md 的形制
把 API 模型放在 `schemas.py`，这两个切片是遗漏。搬运不改任何字段定义，因此
契约快照应零漂移；`contracts.sh --check` 是这条断言的证据。）
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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


__all__ = [
    "ConversationMessageView",
    "ConversationSummary",
    "StreamRequest",
    "TurnResponse",
]
