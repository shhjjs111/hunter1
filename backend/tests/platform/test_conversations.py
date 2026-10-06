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
