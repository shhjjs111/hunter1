"""assistant 切片的 API 模型 —— 跨端契约的**唯一事实来源**。

这些 Pydantic 模型经 OpenAPI 快照（`contracts/openapi.json`）流向
前端类型（`frontend/src/shared/api/schema.d.ts`）。改这里 = 改契约：
改完跑 `bash scripts/contracts.sh` 重新导出并提交快照。

（原先这些模型内联在 `router.py` 里 —— 本仓库其余切片都按 AGENTS.md 的形制
把 API 模型放在 `schemas.py`，这两个切片是遗漏。搬运不改任何字段定义，因此
契约快照应零漂移；`contracts.sh --check` 是这条断言的证据。）
"""

from __future__ import annotations

from datetime import datetime

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
    #: 用 `datetime` 而非 `str`：契约里才会带上 `format: date-time`，与
    #: `ApplicationSummary.updated_at` 等其余切片的同类字段一致。
    #: 早先是 `str` + 构造处手动 `.isoformat()`，于是同一类字段在契约里
    #: 一半是 date-time、一半是**裸 string** —— 前端拿不到格式保证。
    updated_at: datetime


class ConversationMessageView(BaseModel):
    """会话里的一条消息。"""

    role: str
    content: str


class ConversationListResponse(BaseModel):
    """会话列表页。

    `total` / `has_more` 是**截断信号**：列表有固定上限（见 router 的 `LIST_LIMIT`），
    没有它们时第 51 个起的会话永久不可见、且界面看起来「这就是全部」。与
    `applications/schemas.py` 的 `ApplicationListResponse` 同一课 —— 原先这里是
    裸数组，连放截断信号的位置都没有。
    """

    items: list[ConversationSummary]
    total: int
    has_more: bool


__all__ = [
    "ConversationListResponse",
    "ConversationMessageView",
    "ConversationSummary",
    "StreamRequest",
    "TurnResponse",
]
