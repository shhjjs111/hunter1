"""会话仓储 —— 助手对话的持久化。

消息按 `sequence` 单调递增保存；读取默认按时间正序，`limit` 时取**最近** N 条
（对话越长越该保留近期上下文）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from hunter1.domain.assistant import Message, Role, ToolCall
from hunter1.platform.db.schema import ConversationMessageRow, ConversationRow

if TYPE_CHECKING:
    from hunter1.platform.db.database import Database


@dataclass
class Conversation:
    """一段对话的元信息。"""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


class SqliteConversationRepository:
    """会话与消息的 SQLite 仓储。

    时钟可注入 —— 让「按更新时间排序」这类行为能确定性测试，
    不必依赖真实时钟的分辨率（同微秒内两次写入会让排序变得不确定）。
    """

    def __init__(self, database: Database, *, clock: Callable[[], datetime] = _now) -> None:
        self._db = database
        self._clock = clock

    # ---- 会话 ----

    def create(self, *, title: str) -> Conversation:
        conversation_id = uuid.uuid4().hex
        now = self._clock()
        with self._db.session() as session:
            session.add(
                ConversationRow(
                    id=conversation_id, title=title[:255], created_at=now, updated_at=now
                )
            )
            session.commit()
        return Conversation(id=conversation_id, title=title, created_at=now, updated_at=now)

    def get(self, conversation_id: str) -> Conversation | None:
        with self._db.session() as session:
            row = session.get(ConversationRow, conversation_id)
            return _to_conversation(row) if row is not None else None

    def list(self, *, limit: int = 50) -> list[Conversation]:
        with self._db.session() as session:
            statement = (
                select(ConversationRow).order_by(ConversationRow.updated_at.desc()).limit(limit)
            )
            return [_to_conversation(row) for row in session.scalars(statement)]

    def delete(self, conversation_id: str) -> None:
        with self._db.session() as session:
            session.execute(
                delete(ConversationMessageRow).where(
                    ConversationMessageRow.conversation_id == conversation_id
                )
            )
            session.execute(delete(ConversationRow).where(ConversationRow.id == conversation_id))
            session.commit()

    # ---- 消息 ----

    def append(self, conversation_id: str, message: Message) -> None:
        now = self._clock()
        with self._db.session() as session:
            conversation = session.get(ConversationRow, conversation_id)
            if conversation is None:
                raise KeyError(f"conversation not found: {conversation_id}")

            next_seq = (
                session.scalar(
                    select(func.coalesce(func.max(ConversationMessageRow.sequence), 0)).where(
                        ConversationMessageRow.conversation_id == conversation_id
                    )
                )
                or 0
            ) + 1

            session.add(
                ConversationMessageRow(
                    id=uuid.uuid4().hex,
                    conversation_id=conversation_id,
                    sequence=next_seq,
                    role=str(message.role),
                    content=message.content or "",
                    tool_calls=[
                        {"id": call.id, "name": call.name, "arguments": call.arguments}
                        for call in message.tool_calls
                    ],
                    tool_call_id=message.tool_call_id,
                    created_at=now,
                )
            )
            conversation.updated_at = now
            session.commit()

    def messages(self, conversation_id: str, *, limit: int | None = None) -> list[Message]:
        with self._db.session() as session:
            statement = (
                select(ConversationMessageRow)
                .where(ConversationMessageRow.conversation_id == conversation_id)
                .order_by(ConversationMessageRow.sequence.asc())
            )
            rows = list(session.scalars(statement))
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]
        return [_to_message(row) for row in rows]


def _to_conversation(row: ConversationRow) -> Conversation:
    return Conversation(
        id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at
    )


def _to_message(row: ConversationMessageRow) -> Message:
    raw_calls: list[dict[str, Any]] = list(row.tool_calls or [])
    tool_calls = [
        ToolCall(
            id=str(item.get("id", "")),
            name=str(item.get("name", "")),
            arguments=dict(item.get("arguments") or {}),
        )
        for item in raw_calls
        if isinstance(item, dict)
    ]
    return Message(
        role=Role(row.role),
        content=row.content,
        tool_calls=tool_calls,
        tool_call_id=row.tool_call_id,
    )


__all__ = ["Conversation", "SqliteConversationRepository"]
