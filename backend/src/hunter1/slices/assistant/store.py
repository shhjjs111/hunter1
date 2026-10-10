"""assistant 切片的会话持久化门面 —— 薄委托 platform.db 的会话仓储。

切片内唯一接触会话存储的地方；将来换存储（或加缓存）只动这里。
"""

from __future__ import annotations

from collections.abc import Sequence

from hunter1.domain.assistant import Message
from hunter1.platform.db import Database
from hunter1.platform.db.conversations import Conversation


class ConversationStore:
    """会话与消息的存取。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, *, title: str) -> Conversation:
        return self._db.conversations().create(title=title)

    def get(self, conversation_id: str) -> Conversation | None:
        return self._db.conversations().get(conversation_id)

    def list(self, *, limit: int = 50, offset: int = 0) -> list[Conversation]:
        return self._db.conversations().list(limit=limit, offset=offset)

    def count(self) -> int:
        """会话总数 —— 分页要用它算 `has_next`（以及「还有更旧的」）。"""
        return self._db.conversations().count()

    def delete(self, conversation_id: str) -> None:
        self._db.conversations().delete(conversation_id)

    def append_many(self, conversation_id: str, messages: Sequence[Message]) -> None:
        """原子追加一批消息（一轮对话的两条同事务落库，不留半截）。"""
        self._db.conversations().append_many(conversation_id, messages)

    def create_with_messages(self, *, title: str, messages: Sequence[Message]) -> Conversation:
        """同事务新建会话 + 首批消息（新会话的唯一正确开法）。

        不要用 `create()` 再 `append_many()`：那是两个事务，第二步失败就留下一段
        **空会话** —— 侧栏里点进去什么都没有，而且永远不会被填上。
        """
        return self._db.conversations().create_with_messages(title=title, messages=messages)

    def messages(
        self, conversation_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[Message]:
        return self._db.conversations().messages(conversation_id, limit=limit, offset=offset)


__all__ = ["ConversationStore"]
