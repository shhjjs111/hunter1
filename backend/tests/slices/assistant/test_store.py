"""assistant 切片的会话门面（`ConversationStore`）测试 —— 真实 SQLite，离线。

**这不是 platform 层仓储的测试** —— 那份在 `tests/platform/test_conversations.py`。
本文件此前整篇都是那份的重复覆盖，而且一次都没 import 它名字里的
`ConversationStore`：切片门面于是**零直接覆盖**（把 `list()` 的默认 `limit=50`
改成 5，全仓测试照样绿）。现在它测的是门面自身的契约。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hunter1.domain.assistant import Message, Role, ToolCall
from hunter1.platform.db import Database
from hunter1.slices.assistant.store import ConversationStore


@pytest.fixture()
def store(tmp_path: Path) -> ConversationStore:
    db = Database(tmp_path / "conv.db")
    db.initialize()
    return ConversationStore(db)


class TestConversationFacade:
    def test_create_get_delete(self, store: ConversationStore) -> None:
        conversation = store.create(title="找工作咨询")
        assert conversation.id
        assert store.get(conversation.id) is not None
        store.delete(conversation.id)
        assert store.get(conversation.id) is None

    def test_get_missing_returns_none(self, store: ConversationStore) -> None:
        assert store.get("nope") is None

    def test_list_default_limit_is_50(self, store: ConversationStore) -> None:
        """`list()` 的默认上限是**契约的一部分**（路由据此给会话列表）。

        此前没有任何用例钉它：默认值改成 5 也全绿，而「会话列表只显示最近 5 条」
        这种退化要等用户报上来才会被发现。
        """
        for index in range(60):
            store.create(title=f"会话{index}")
        assert len(store.list()) == 50

    def test_list_respects_explicit_limit(self, store: ConversationStore) -> None:
        for index in range(10):
            store.create(title=f"会话{index}")
        assert len(store.list(limit=3)) == 3


class TestMessagesThroughFacade:
    def test_append_and_read_back(self, store: ConversationStore) -> None:
        conversation = store.create(title="t")
        store.append(conversation.id, Message(Role.USER, "帮我找产品岗"))
        store.append(conversation.id, Message(Role.ASSISTANT, "好的，我来查。"))
        messages = store.messages(conversation.id)
        assert [m.role for m in messages] == [Role.USER, Role.ASSISTANT]
        assert messages[0].content == "帮我找产品岗"

    def test_append_many_writes_the_pair(self, store: ConversationStore) -> None:
        """一轮对话的两条消息同事务落库 —— 不留「有问无答」的半截。"""
        conversation = store.create(title="t")
        store.append_many(
            conversation.id,
            [Message(Role.USER, "帮我找产品岗"), Message(Role.ASSISTANT, "好的。")],
        )
        assert [m.role for m in store.messages(conversation.id)] == [Role.USER, Role.ASSISTANT]

    def test_append_many_to_missing_conversation_raises(self, store: ConversationStore) -> None:
        with pytest.raises(KeyError):
            store.append_many("ghost", [Message(Role.USER, "你好")])

    def test_messages_limit_returns_most_recent(self, store: ConversationStore) -> None:
        conversation = store.create(title="t")
        for index in range(10):
            store.append(conversation.id, Message(Role.USER, f"m{index}"))
        assert [m.content for m in store.messages(conversation.id, limit=3)] == [
            "m7",
            "m8",
            "m9",
        ]

    def test_tool_call_roundtrip(self, store: ConversationStore) -> None:
        conversation = store.create(title="t")
        store.append(
            conversation.id,
            Message(
                Role.ASSISTANT,
                "",
                tool_calls=[ToolCall(id="c1", name="search_jobs", arguments={"keyword": "产品"})],
            ),
        )
        messages = store.messages(conversation.id)
        assert messages[0].tool_calls[0].name == "search_jobs"
        assert messages[0].tool_calls[0].arguments == {"keyword": "产品"}

    def test_messages_survive_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "persist.db"
        first = Database(path)
        first.initialize()
        conversation = ConversationStore(first).create(title="t")
        ConversationStore(first).append(conversation.id, Message(Role.USER, "持久化的消息"))

        reopened = Database(path)
        reopened.initialize()
        messages = ConversationStore(reopened).messages(conversation.id)
        assert [m.content for m in messages] == ["持久化的消息"]
