"""会话仓储 —— 助手对话的持久化。

消息按 `sequence` 单调递增保存；读取默认按时间正序，`limit` 时取**最近** N 条
（对话越长越该保留近期上下文）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from hunter1.domain.assistant import Message, Role, ToolCall
from hunter1.platform.db.enums import restore_enum
from hunter1.platform.db.schema import ConversationMessageRow, ConversationRow

if TYPE_CHECKING:
    from hunter1.platform.db.database import Database


def _is_sequence_conflict(exc: IntegrityError) -> bool:
    """该完整性错误是否由 `(conversation_id, sequence)` 撞号引起。

    只对撞号重试。FK 失败（如会话被并发删除）等其它完整性错误必须**直接抛出** ——
    否则会被误报成「并发写入冲突过多」，把真实原因埋掉。

    判别钉到**列级**而非表级：表上将来若再添第二个唯一约束，撞了那个也会落进
    「UNIQUE constraint failed」的宽匹配，被误当撞号重试。
    """
    message = str(exc.orig)
    return "UNIQUE constraint failed" in message and (
        "conversation_messages.conversation_id" in message
    )


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

    #: 序号撞号（并发 append 各算一次 `max+1`）时的重试次数。真正并发窗口极窄，
    #: 几次足够；耗尽说明冲突异常频繁，宁可报错也不静默丢消息。
    _MAX_SEQUENCE_RETRIES = 5

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

    def list(self, *, limit: int = 50, offset: int = 0) -> list[Conversation]:
        # 第二键 id：多个会话可能同 updated_at（时钟注入、批量创建），同值行顺序
        # SQL 不作保证。理由见 repository.py 的 _JOB_ORDER。
        #
        # `offset` 必须配一个**确定的** ORDER BY（上面就是）—— 否则分页会漏行/重行。
        with self._db.session() as session:
            statement = (
                select(ConversationRow)
                .order_by(ConversationRow.updated_at.desc(), ConversationRow.id.asc())
                .limit(limit)
                .offset(offset)
            )
            return [_to_conversation(row) for row in session.scalars(statement)]

    def count(self) -> int:
        """会话总数 —— 列表有上限，没它就无从知道「被截断了没有」。"""
        with self._db.session() as session:
            total = session.scalar(select(func.count()).select_from(ConversationRow))
            return int(total or 0)

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
        """追加一条消息（委托 `append_many`，保证序号唯一且同事务）。"""
        self.append_many(conversation_id, [message])

    def append_many(self, conversation_id: str, messages: Sequence[Message]) -> None:
        """**原子地**追加一批消息（一轮对话的用户消息 + 回复）。

        - **同事务**：要么全部落库、要么一条不留 —— 否则失败会留下「有问无答」的
          半截对话，下次把失败那句当上下文再问一遍。
        - **序号唯一**：`max(sequence)+1` 是读-改-写的，并发 append 会算出同一个号；
          撞号时数据库唯一索引（见 `Database.initialize`）报 `IntegrityError`，
          这里重算重试。序号单调由索引兜底，不靠「恰好没并发」。
        """
        if not messages:
            return
        now = self._clock()
        last_error: IntegrityError | None = None
        for _ in range(self._MAX_SEQUENCE_RETRIES):
            try:
                with self._db.session() as session:
                    conversation = session.get(ConversationRow, conversation_id)
                    if conversation is None:
                        raise KeyError(f"conversation not found: {conversation_id}")

                    next_seq = (
                        session.scalar(
                            select(
                                func.coalesce(func.max(ConversationMessageRow.sequence), 0)
                            ).where(ConversationMessageRow.conversation_id == conversation_id)
                        )
                        or 0
                    ) + 1

                    for offset, message in enumerate(messages):
                        session.add(
                            ConversationMessageRow(
                                id=uuid.uuid4().hex,
                                conversation_id=conversation_id,
                                sequence=next_seq + offset,
                                role=str(message.role),
                                content=message.content or "",
                                tool_calls=[
                                    {
                                        "id": call.id,
                                        "name": call.name,
                                        "arguments": call.arguments,
                                    }
                                    for call in message.tool_calls
                                ],
                                tool_call_id=message.tool_call_id,
                                created_at=now,
                            )
                        )
                    conversation.updated_at = now
                    session.commit()
                return
            except IntegrityError as exc:
                if not _is_sequence_conflict(exc):
                    raise
                last_error = exc
                continue
        raise RuntimeError(
            f"无法为会话 {conversation_id} 分配消息序号：并发写入冲突过多"
        ) from last_error

    def messages(self, conversation_id: str, *, limit: int | None = None) -> list[Message]:
        with self._db.session() as session:
            statement = (
                select(ConversationMessageRow)
                .where(ConversationMessageRow.conversation_id == conversation_id)
                .order_by(ConversationMessageRow.sequence.asc())
            )
            rows = list(session.scalars(statement))
        if limit is not None:
            # `rows[-limit:]` 在 limit=0 时是 `rows[-0:]` —— 等于**全部**：
            # `assistant_history_limit` 若被配成 0（意图「不带历史」），会静默变成
            # 「带全部历史」，正好与意图相反。负数同理给出错误窗口。显式返回空。
            if limit <= 0:
                return []
            if len(rows) > limit:
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
        role=restore_enum(Role, row.role, default=Role.USER, where="会话消息角色"),
        content=row.content,
        tool_calls=tool_calls,
        tool_call_id=row.tool_call_id,
    )


__all__ = ["Conversation", "SqliteConversationRepository"]
