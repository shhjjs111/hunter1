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

# 单条对话消息的字符上限。
#
# `/assistant/turn` 与 `/assistant/stream` 都把 `message` 直接送进 LLM —— 这是全仓
# **唯一没有闸门就直通模型**的大输入：请求体、提示词长度和费用一起放大，而且
# `min_length=1` 只挡空串。一次手滑（把整个文件粘进输入框）或一个失控的脚本就够。
# 8000 是「够贴一段 JD / 一份简历摘要」的量级（对比 `JD_PREVIEW_LIMIT = 1500`）；
# 超过它的基本不是「对话」而是「上传」，那该走别的入口。
MAX_MESSAGE_CHARS = 8000


class StreamRequest(BaseModel):
    """对话请求体（流式与一次性共用）。"""

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
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

    会话数量没有天然上限，所以这里是**真分页**：`page` / `page_size` / `has_next`
    让第 N 页起的会话都够得着。之前只有 `total` / `has_more`（截断信号）而没有翻页
    入口 —— 那时第 51 个起的会话不只是不可见，而是**完全无法触达**：界面能说
    「还有更多」，却没有任何办法把它取出来。

    与 `jobs` 的 `JobListResponse` 同一形状。`has_next` 比 `has_more` 少一层歧义：
    停在第 3 页时，「more」指「这一页之后还有」还是「比这一页更多」是含糊的。
    """

    items: list[ConversationSummary]
    total: int
    page: int
    page_size: int
    has_next: bool


__all__ = [
    "ConversationListResponse",
    "ConversationMessageView",
    "ConversationSummary",
    "StreamRequest",
    "TurnResponse",
]
