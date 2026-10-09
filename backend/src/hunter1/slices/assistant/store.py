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

    def list(self, *, limit: int = 50) -> list[Conversation]:
        return self._db.conversations().list(limit=limit)

    def count(self) -> int:
        """会话总数 —— 列表有固定上限，用它算截断信号。"""
        return self._db.conversations().count()

    def delete(self, conversation_id: str) -> None:
        self._db.conversations().delete(conversation_id)

    def append(self, conversation_id: str, message: Message) -> None:
        self._db.conversations().append(conversation_id, message)

    def append_many(self, conversation_id: str, messages: Sequence[Message]) -> None:
        """原子追加一批消息（一轮对话的两条同事务落库，不留半截）。"""
        self._db.conversations().append_many(conversation_id, messages)

    def messages(self, conversation_id: str, *, limit: int | None = None) -> list[Message]:
        return self._db.conversations().messages(conversation_id, limit=limit)


__all__ = ["ConversationStore"]
