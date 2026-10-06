"""会话持久化测试 —— 真实 SQLite，离线。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter1.domain.assistant import Message, Role
from hunter1.platform.db import Database


class FakeClock:
    """手动推进的时钟 —— 让排序行为确定性可测，不依赖真实时钟分辨率。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "conv.db")
    database.initialize()
    return database


def _repo(db: Database, clock: FakeClock):
    from hunter1.platform.db import SqliteConversationRepository

    return SqliteConversationRepository(db, clock=clock)


class TestConversationLifecycle:
    def test_create_and_get(self, db: Database) -> None:
        repo = db.conversations()
        conv = repo.create(title="找工作咨询")
        assert conv.id
        assert conv.title == "找工作咨询"
        assert repo.get(conv.id) is not None

    def test_list_orders_by_updated_desc(self, db: Database) -> None:
        clock = FakeClock()
        repo = _repo(db, clock)
        first = repo.create(title="先建的")
        second = repo.create(title="后建的")
        repo.append(first.id, Message(Role.USER, "新消息"))  # 触碰 first
        listed = repo.list()
        assert [c.id for c in listed][:2] == [first.id, second.id]

    def test_delete_removes_conversation_and_messages(self, db: Database) -> None:
        repo = db.conversations()
        conv = repo.create(title="待删")
        repo.append(conv.id, Message(Role.USER, "你好"))
        repo.delete(conv.id)
        assert repo.get(conv.id) is None
        assert repo.messages(conv.id) == []

    def test_get_missing_returns_none(self, db: Database) -> None:
        assert db.conversations().get("nope") is None


class TestMessages:
    def test_append_and_read_back(self, db: Database) -> None:
        repo = db.conversations()
        conv = repo.create(title="t")
        repo.append(conv.id, Message(Role.USER, "帮我找产品岗"))
        repo.append(conv.id, Message(Role.ASSISTANT, "好的，我来查。"))
        msgs = repo.messages(conv.id)
        assert [m.role for m in msgs] == [Role.USER, Role.ASSISTANT]
        assert msgs[0].content == "帮我找产品岗"

    def test_order_is_preserved(self, db: Database) -> None:
        repo = db.conversations()
        conv = repo.create(title="t")
        for index in range(5):
            repo.append(conv.id, Message(Role.USER, f"第 {index} 条"))
        assert [m.content for m in repo.messages(conv.id)] == [
            f"第 {index} 条" for index in range(5)
        ]

    def test_tool_call_roundtrip(self, db: Database) -> None:
        from hunter1.domain.assistant import ToolCall

        repo = db.conversations()
        conv = repo.create(title="t")
        repo.append(
            conv.id,
            Message(
                Role.ASSISTANT,
                "",
                tool_calls=[ToolCall(id="c1", name="search_jobs", arguments={"keyword": "产品"})],
            ),
        )
        repo.append(conv.id, Message(Role.TOOL, "找到 2 条", tool_call_id="c1"))
        msgs = repo.messages(conv.id)
        assert msgs[0].tool_calls[0].name == "search_jobs"
        assert msgs[0].tool_calls[0].arguments == {"keyword": "产品"}
        assert msgs[1].tool_call_id == "c1"

    def test_append_touches_updated_at(self, db: Database) -> None:
        repo = db.conversations()
        conv = repo.create(title="t")
        before = repo.get(conv.id)
        assert before is not None
        repo.append(conv.id, Message(Role.USER, "新消息"))
        after = repo.get(conv.id)
        assert after is not None
        assert after.updated_at >= before.updated_at

    def test_append_to_missing_conversation_raises(self, db: Database) -> None:
        with pytest.raises(KeyError):
            db.conversations().append("ghost", Message(Role.USER, "你好"))

    def test_messages_survive_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "persist.db"
        first = Database(path)
        first.initialize()
        conv = first.conversations().create(title="t")
        first.conversations().append(conv.id, Message(Role.USER, "持久化的消息"))

        reopened = Database(path)
        reopened.initialize()
        msgs = reopened.conversations().messages(conv.id)
        assert len(msgs) == 1
        assert msgs[0].content == "持久化的消息"

    def test_limit_returns_most_recent(self, db: Database) -> None:
        repo = db.conversations()
        conv = repo.create(title="t")
        for index in range(10):
            repo.append(conv.id, Message(Role.USER, f"m{index}"))
        recent = repo.messages(conv.id, limit=3)
        assert [m.content for m in recent] == ["m7", "m8", "m9"]


class FakeClockAdvance(FakeClock):
    """每次调用**不**推进的时钟（append_many 内部只取一次时间）。"""

    def __call__(self) -> datetime:
        return self.now


class TestSequenceIntegrity:
    """(conversation_id, sequence) 必须唯一 —— 并发 append 撞号要被数据库拒绝。

    报告里的一类竞态：两个 append 各自 `max(sequence)+1` 得到同一个号，插进去两条
    同号消息，读回顺序错乱、历史被静默污染。修法：数据库层唯一索引兜底 + 写入侧
    撞号重试。
    """

    def test_duplicate_sequence_is_rejected(self, db: Database) -> None:
        from sqlalchemy.exc import IntegrityError

        from hunter1.platform.db.schema import ConversationMessageRow

        repo = db.conversations()
        conv = repo.create(title="t")
        repo.append(conv.id, Message(Role.USER, "第一条"))  # sequence = 1

        with pytest.raises(IntegrityError), db.session() as session:
            session.add(
                ConversationMessageRow(
                    id="dup",
                    conversation_id=conv.id,
                    sequence=1,  # 撞号
                    role=str(Role.USER),
                    content="撞号",
                    tool_calls=[],
                    tool_call_id=None,
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()

    def test_sequences_are_contiguous(self, db: Database) -> None:
        from sqlalchemy import select

        from hunter1.platform.db.schema import ConversationMessageRow

        repo = db.conversations()
        conv = repo.create(title="t")
        for index in range(5):
            repo.append(conv.id, Message(Role.USER, f"m{index}"))
        with db.session() as session:
            seqs = list(
                session.scalars(
                    select(ConversationMessageRow.sequence)
                    .where(ConversationMessageRow.conversation_id == conv.id)
                    .order_by(ConversationMessageRow.sequence)
                )
            )
        assert seqs == [1, 2, 3, 4, 5]


class TestAppendMany:
    def test_saves_all_in_order(self, db: Database) -> None:
        repo = db.conversations()
        conv = repo.create(title="t")
        repo.append_many(conv.id, [Message(Role.USER, "问"), Message(Role.ASSISTANT, "答")])
        assert [m.content for m in repo.messages(conv.id)] == ["问", "答"]

    def test_is_atomic_on_missing_conversation(self, db: Database) -> None:
        """一轮对话的用户消息与回复要么都落库，要么都不落 —— 不留半截对话。"""
        repo = db.conversations()
        with pytest.raises(KeyError):
            repo.append_many("ghost", [Message(Role.USER, "a"), Message(Role.ASSISTANT, "b")])
        assert repo.messages("ghost") == []

    def test_touches_updated_at_once(self, db: Database) -> None:
        clock = FakeClockAdvance()
        repo = _repo(db, clock)
        conv = repo.create(title="t")
        before = repo.get(conv.id)
        assert before is not None
        repo.append_many(conv.id, [Message(Role.USER, "问"), Message(Role.ASSISTANT, "答")])
        after = repo.get(conv.id)
        assert after is not None
        assert after.updated_at >= before.updated_at
