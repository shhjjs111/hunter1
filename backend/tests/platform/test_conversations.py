"""会话持久化测试 —— 真实 SQLite，离线。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
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

    def test_list_is_stable_when_updated_at_ties(self, db: Database) -> None:
        """多个会话同 updated_at 时，顺序必须确定 —— 同值行的顺序不是「碰巧怎么返回」。"""
        from hunter1.platform.db import SqliteConversationRepository

        frozen = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
        repo = SqliteConversationRepository(db, clock=lambda: frozen)
        ids = [repo.create(title=f"t{index}").id for index in range(4)]
        assert [c.id for c in repo.list()] == sorted(ids)

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

    def test_limit_zero_means_no_history(self, db: Database) -> None:
        """`limit=0` 必须返回空。

        `rows[-0:]` 等于**全部** —— 把 `assistant_history_limit` 配成 0
        （意图「不带历史」）会静默变成「带全部历史」，正好与意图相反。
        """
        repo = db.conversations()
        conv = repo.create(title="t")
        for index in range(4):
            repo.append(conv.id, Message(Role.USER, f"m{index}"))
        assert repo.messages(conv.id, limit=0) == []

    def test_negative_limit_does_not_slice_a_wrong_window(self, db: Database) -> None:
        """负的窗口参数**拒绝**，而不是兜成一个空窗口。

        （这条原先断言 `== []`：那时把负值当「空窗口」静默兜住。但 SQLite 的
        `LIMIT -1` 是**不限量** —— 兜成空窗口会把「调用方算错了」这件事盖住，
        而分页窗口悄悄变样的表现是漏行/重行，比报错难查得多。故收紧为报错。）
        """
        repo = db.conversations()
        conv = repo.create(title="t")
        for index in range(4):
            repo.append(conv.id, Message(Role.USER, f"m{index}"))
        with pytest.raises(ValueError):
            repo.messages(conv.id, limit=-1)
        with pytest.raises(ValueError):
            repo.messages(conv.id, limit=2, offset=-1)
        with pytest.raises(ValueError):
            repo.list(limit=-1)


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


class TestRepairOfLegacyDuplicates:
    """老库在索引落地前撞过号留下重复行时，启动自愈而不是把应用挡在门外。"""

    def _make_dirty(self, db: Database) -> None:
        import sqlalchemy as sa

        with db.engine.begin() as conn:
            conn.execute(sa.text("DROP INDEX IF EXISTS uq_conv_messages_conv_seq"))
            conn.execute(
                sa.text(
                    "INSERT INTO conversations (id,title,created_at,updated_at) "
                    "VALUES ('c1','t','2026-01-01 00:00:00','2026-01-01 00:00:00')"
                )
            )
            for mid in ("m1", "m2"):
                conn.execute(
                    sa.text(
                        "INSERT INTO conversation_messages "
                        "(id,conversation_id,sequence,role,content,tool_calls,"
                        "tool_call_id,created_at) "
                        "VALUES (:mid,'c1',1,'user',:mid,'[]',NULL,'2026-01-01 00:00:00')"
                    ),
                    {"mid": mid},
                )

    def test_initialize_heals_instead_of_crashing(self, db: Database) -> None:
        self._make_dirty(db)
        db.initialize()  # 不应抛裸 IntegrityError
        assert len(db.conversations().messages("c1")) == 2

    def test_cleanish_database_reports_zero_repairs(self, db: Database) -> None:
        """干净库不触发修复（零写入）—— 返回值供启动日志判断是否要提示。"""

        with db.engine.begin() as conn:
            repaired = Database._repair_duplicate_message_sequences(conn)
        assert repaired == 0

    def test_dirty_database_reports_repair_count(self, db: Database) -> None:
        """修复要能被计数 —— 启动日志据此说「已重排 N 个会话」。"""

        self._make_dirty(db)
        with db.engine.begin() as conn:
            repaired = Database._repair_duplicate_message_sequences(conn)
        assert repaired == 1

    def test_stale_non_unique_index_is_dropped(self, db: Database) -> None:
        """升级后旧的非唯一索引要被清掉 —— 否则同列留下两个索引（冗余）。

        真实旧库的形态：`ix_conv_messages_conv_seq`（CREATE INDEX，非唯一）。
        升级演练在 backend/.data/hunter1.db 上确认过它会残留。
        """
        import sqlalchemy as sa

        with db.engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_conv_messages_conv_seq "
                    "ON conversation_messages (conversation_id, sequence)"
                )
            )
        db.initialize()
        with db.session() as session:
            names = set(
                session.scalars(
                    sa.text(
                        "SELECT name FROM sqlite_master WHERE type='index' "
                        "AND tbl_name='conversation_messages'"
                    )
                )
            )
        assert "uq_conv_messages_conv_seq" in names
        assert "ix_conv_messages_conv_seq" not in names

    def test_sequences_become_unique_and_ordered(self, db: Database) -> None:
        import sqlalchemy as sa

        self._make_dirty(db)
        db.initialize()
        with db.session() as session:
            seqs = list(
                session.scalars(
                    sa.text(
                        "SELECT sequence FROM conversation_messages "
                        "WHERE conversation_id='c1' ORDER BY sequence"
                    )
                )
            )
        assert seqs == [1, 2]

    def test_unique_index_present_after_healing(self, db: Database) -> None:
        import sqlalchemy as sa

        self._make_dirty(db)
        db.initialize()
        with db.session() as session:
            found = list(
                session.scalars(
                    sa.text(
                        "SELECT name FROM sqlite_master WHERE type='index' "
                        "AND name='uq_conv_messages_conv_seq'"
                    )
                )
            )
        assert found == ["uq_conv_messages_conv_seq"]


class TestSequenceConflictClassification:
    def test_only_unique_conflicts_are_retryable(self) -> None:
        """FK 之类的完整性错误不该被当成撞号重试（否则误报「并发冲突过多」）。"""
        import sqlite3

        from sqlalchemy.exc import IntegrityError

        from hunter1.platform.db.conversations import _is_sequence_conflict

        unique = IntegrityError(
            "stmt",
            {},
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: conversation_messages.conversation_id, "
                "conversation_messages.sequence"
            ),
        )
        foreign_key = IntegrityError(
            "stmt", {}, sqlite3.IntegrityError("FOREIGN KEY constraint failed")
        )
        # 表上将来若添了别的唯一约束，撞了它也不该被当撞号重试
        other_unique = IntegrityError(
            "stmt", {}, sqlite3.IntegrityError("UNIQUE constraint failed: other.col")
        )
        assert _is_sequence_conflict(unique) is True
        assert _is_sequence_conflict(foreign_key) is False
        assert _is_sequence_conflict(other_unique) is False


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


class TestMessageWindow:
    """`messages(limit/offset)`：窗口**从最新往回数**，且把 LIMIT 下推到 SQL。

    会话没有删除端点，消息只增不减 —— 既要有上限，又不能因此让旧消息够不着。
    """

    def _conversation(self, db: Database):  # type: ignore[no-untyped-def]
        repo = _repo(db, FakeClock())
        conversation = repo.create(title="会话")
        repo.append_many(
            conversation.id,
            [Message(role=Role.USER, content=f"m{i}") for i in range(1, 7)],
        )
        return repo, conversation.id

    def test_limit_returns_the_newest_still_in_chronological_order(self, db: Database) -> None:
        repo, cid = self._conversation(db)
        assert [m.content for m in repo.messages(cid, limit=2)] == ["m5", "m6"]

    def test_offset_counts_back_from_the_newest(self, db: Database) -> None:
        """从最新往回数，所以旧消息够得着 —— 上限不会让历史变得够不着。"""
        repo, cid = self._conversation(db)
        assert [m.content for m in repo.messages(cid, limit=2, offset=2)] == ["m3", "m4"]

    def test_zero_limit_still_means_no_history(self, db: Database) -> None:
        """`limit=0` 的语义保持不变：不要历史。别被这次的窗口改动带偏。"""
        repo, cid = self._conversation(db)
        assert repo.messages(cid, limit=0) == []


class TestCreateTitle:
    def test_returned_title_is_the_stored_one(self, db: Database) -> None:
        """落库要截断到列宽，**返回值**也必须是截断后的。

        否则调用方拿到一个永远读不回来的标题：界面显示 A、重新加载变成 B。
        """
        repo = _repo(db, FakeClock())
        created = repo.create(title="标" * 600)
        assert len(created.title) == 255
        stored = repo.get(created.id)
        assert stored is not None and stored.title == created.title


class TestToolCallHydration:
    def test_non_mapping_arguments_do_not_break_the_whole_read(self) -> None:
        """库里一条坏值不该让整段对话读不出来（`dict("ab")` 会抛 ValueError）。"""
        from types import SimpleNamespace

        from hunter1.platform.db.conversations import _to_message

        row = SimpleNamespace(
            role="user",
            content="问",
            tool_calls=[
                {"id": "c1", "name": "t", "arguments": "not-a-mapping"},
                {"id": "c2", "name": "t2", "arguments": {"keyword": "产品"}},
            ],
            tool_call_id=None,
        )
        message = _to_message(row)  # type: ignore[arg-type]
        assert [call.arguments for call in message.tool_calls] == [{}, {"keyword": "产品"}]
