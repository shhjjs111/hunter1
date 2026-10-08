"""assistant 切片 HTTP 面测试 —— 真 SQLite + TestClient + 脚本化假模型，全程离线。

覆盖三件容易漏的事：
- 会话列表与消息（含 404）；
- SSE 事件序列（text → tool_start → tool_end → done）；
- **失败也走事件流**（error 事件），且失败不留空会话。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hunter1.domain.assistant import Message, Role, ToolCall
from hunter1.domain.llm import LLMError, LLMResponse, StreamComplete, TextDelta
from hunter1.platform.db import Database
from hunter1.slices.assistant.job_tools import build_tools
from hunter1.slices.assistant.router import build_router
from hunter1.slices.assistant.store import ConversationStore


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        payload = block[len("data:") :].strip()
        if payload:
            events.append(json.loads(payload))
    return events


class ScriptedLLM:
    """按脚本作答：第一轮请求工具，第二轮给出结论。

    `calls` 记录每次调用收到的 `messages` —— 多轮记忆（第二轮必须带上第一轮）
    只能从**送进模型的东西**上验证，光看回复是看不出来的。
    """

    def __init__(self, *, reply: str = "共 1 条岗位。", boom: bool = False) -> None:
        self.reply = reply
        self.boom = boom
        self.round = 0
        self.closed = False
        self.calls: list[list[Message]] = []

    def close(self) -> None:
        """端口要求：用完释放（真实客户端会关掉 httpx 连接池）。"""
        self.closed = True

    # 一次性路径
    def complete_with_tools(self, *, messages: list[Message], **_kw: Any) -> LLMResponse:
        self.calls.append(list(messages))
        if self.boom:
            raise LLMError("upstream_failed", "模型不可用")
        self.round += 1
        if self.round == 1:
            return LLMResponse(
                content="",
                model="fake",
                tool_calls=[ToolCall(id="c1", name="search_jobs", arguments={"keyword": "产品"})],
            )
        return LLMResponse(content=self.reply, model="fake")

    # 流式路径（端口契约：以 StreamComplete 收尾）
    def stream_with_tools(self, *, messages: list[Message], **_kw: Any) -> Iterator[Any]:
        self.calls.append(list(messages))
        if self.boom:
            raise LLMError("upstream_failed", "模型不可用")
        self.round += 1
        if self.round == 1:
            yield StreamComplete(
                content="",
                model="fake",
                tool_calls=[ToolCall(id="c1", name="search_jobs", arguments={"keyword": "产品"})],
            )
            return
        for chunk in ("共 ", "1 条", "岗位。"):
            yield TextDelta(chunk)
        yield StreamComplete(content=self.reply, model="fake")


class MidStreamFailureLLM:
    """吐出一段文本**之后**失败 —— 模拟网关用 SSE `error` 帧报错。"""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def stream_with_tools(self, **_kw: Any) -> Iterator[Any]:
        yield TextDelta("半截回答")
        raise LLMError("stream_error", "Insufficient Balance")


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "assistant.db")
    database.initialize()
    from hunter1.domain.models import Job

    database.jobs().upsert(
        Job(
            id="j1" + "0" * 30,
            company_id="c1",
            title="AI产品经理",
            detail_url="https://x/1",
            source="实习僧",
            company_name="字节跳动",
        )
    )
    return database


def _client(
    db: Database,
    llm: ScriptedLLM | MidStreamFailureLLM,
    *,
    history_limit: int | None = None,
) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(
        build_router(
            store=ConversationStore(db),
            llm_factory=lambda: llm,
            tools=build_tools(jobs=db.jobs(), applications=db.applications()),
            **({} if history_limit is None else {"history_limit": history_limit}),
        ),
        prefix="/api",
    )
    with TestClient(app) as test_client:
        yield test_client


class TestConversationsEndpoints:
    def test_empty_list(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            assert client.get("/api/assistant/conversations").json() == []

    def test_list_after_turn(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            client.post("/api/assistant/turn", json={"message": "有哪些产品岗？"})
            items = client.get("/api/assistant/conversations").json()
            assert len(items) == 1
            assert "有哪些产品岗" in items[0]["title"]

    def test_messages_of_unknown_conversation_is_404(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            assert client.get("/api/assistant/conversations/nope").status_code == 404

    def test_messages_after_turn(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            client.post("/api/assistant/turn", json={"message": "有哪些产品岗？"})
            conversation_id = client.get("/api/assistant/conversations").json()[0]["id"]
            messages = client.get(f"/api/assistant/conversations/{conversation_id}").json()
            assert [item["role"] for item in messages] == ["user", "assistant"]
            assert messages[0]["content"] == "有哪些产品岗？"


class TestTurnEndpoint:
    def test_returns_reply_and_persists(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM(reply="查到 1 条。")):  # type: ignore[arg-type]
            response = client.post("/api/assistant/turn", json={"message": "有哪些产品岗？"})
            assert response.status_code == 200
            payload = response.json()
            assert payload["reply"] == "查到 1 条。"
            assert payload["iterations"] == 2  # 一次工具调用 + 一次结论
            assert db.conversations().list() != []

    def test_model_failure_returns_422_and_creates_no_conversation(self, db: Database) -> None:
        """失败不留空会话 —— 否则下次会把失败那句当上下文再问一遍。

        状态码与 scoring 切片对齐（上游模型失败 → 422 + 可读原因），原先这里是 502。
        """
        for client in _client(db, ScriptedLLM(boom=True)):
            response = client.post("/api/assistant/turn", json={"message": "你好"})
            assert response.status_code == 422
            assert "upstream_failed" in response.json()["detail"]
            assert db.conversations().list() == []

    def test_non_contract_failure_is_not_masked_as_upstream(self, db: Database) -> None:
        """实现 bug（TypeError 之类）必须响亮地失败，不能伪装成「上游故障」。

        端口契约是「失败抛 LLMError」；抛别的说明是实现坏了，给 422/502 会让人
        拿着错误的线索去查网络与厂商。
        """

        class BrokenLLM(ScriptedLLM):
            def complete_with_tools(self, **_kw: Any) -> LLMResponse:
                raise TypeError("实现 bug")

        llm = BrokenLLM()
        for client in _client(db, llm):
            with pytest.raises(TypeError):
                client.post("/api/assistant/turn", json={"message": "你好"})
        assert llm.closed is True  # 失败路径同样要释放客户端

    def test_blank_message_is_rejected(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            assert client.post("/api/assistant/turn", json={"message": "   "}).status_code == 422


class TestStreamEndpoint:
    def test_event_sequence_and_persistence(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            response = client.post("/api/assistant/stream", json={"message": "有哪些产品岗？"})
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")

            events = _parse_sse(response.text)
            kinds = [event["type"] for event in events]
            # 工具调用前后各有事件；done 必须是最后一个
            assert kinds == ["tool_start", "tool_end", "text", "text", "text", "done"]
            assert events[-1]["reply"] == "共 1 条岗位。"
            assert events[0]["name"] == "search_jobs"
            assert events[1]["ok"] is True
            # 「产品」应命中岗位库里的 AI产品经理
            assert "AI产品经理" in events[1]["content"]

            # 整轮跑完才落库
            conversation_id = events[-1]["conversation_id"]
            messages = client.get(f"/api/assistant/conversations/{conversation_id}").json()
            assert [item["role"] for item in messages] == ["user", "assistant"]

    def test_reuses_given_conversation(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            first = _parse_sse(
                client.post("/api/assistant/stream", json={"message": "第一问"}).text
            )[-1]["conversation_id"]
            second = _parse_sse(
                client.post(
                    "/api/assistant/stream",
                    json={"message": "第二问", "conversation_id": first},
                ).text
            )[-1]["conversation_id"]
            assert second == first
            assert len(db.conversations().list()) == 1

    def test_model_failure_becomes_error_event(self, db: Database) -> None:
        """响应已经开始 —— 失败只能以 error 事件交出去，不能悄悄断掉。"""
        for client in _client(db, ScriptedLLM(boom=True)):
            events = _parse_sse(client.post("/api/assistant/stream", json={"message": "你好"}).text)
            assert [event["type"] for event in events] == ["error"]
            assert "upstream_failed" in events[0]["message"]
            # 失败不落库（否则会留下半截会话）
            assert db.conversations().list() == []

    def test_blank_message_is_an_error_event(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            events = _parse_sse(client.post("/api/assistant/stream", json={"message": "   "}).text)
            assert events == [{"type": "error", "message": "请输入内容后再发送。"}]


class TestLlmClientIsReleased:
    """两个端点都每请求新建客户端（改配置要立刻生效），必须用完即关。

    流式那条尤其要紧：真正的消费发生在响应体被读取时，端点函数早已返回 ——
    释放只能挂在 generator 的 finally 上（客户端中断连接时 Starlette 会 close
    这个 generator，同样会走到）。
    """

    def test_turn_releases_client(self, db: Database) -> None:
        llm = ScriptedLLM()
        for client in _client(db, llm):
            assert (
                client.post("/api/assistant/turn", json={"message": "有哪些产品岗？"}).status_code
                == 200
            )
        assert llm.closed is True, "一次性端点必须释放客户端"

    def test_turn_releases_client_on_failure(self, db: Database) -> None:
        llm = ScriptedLLM(boom=True)
        for client in _client(db, llm):
            assert client.post("/api/assistant/turn", json={"message": "在吗"}).status_code == 422
        assert llm.closed is True, "失败也必须释放客户端"

    def test_stream_releases_client(self, db: Database) -> None:
        llm = ScriptedLLM()
        for client in _client(db, llm):
            response = client.post("/api/assistant/stream", json={"message": "有哪些产品岗？"})
            assert response.status_code == 200
            # 读完响应体才算真正消费完（释放挂在 generator 的 finally 上）
            assert response.text
        assert llm.closed is True, "流式端点必须释放客户端"

    def test_stream_releases_client_when_generator_closed_early(self, db: Database) -> None:
        """客户端中途断开 → Starlette close 这个 generator → finally 仍要释放。"""
        llm = ScriptedLLM()
        for client in _client(db, llm):
            with client.stream(
                "POST", "/api/assistant/stream", json={"message": "有哪些产品岗？"}
            ) as r:
                assert r.status_code == 200
                next(r.iter_text())  # 只读第一块就撒手
        assert llm.closed is True, "客户端提前断开也必须释放"


class TestMultiTurnHistory:
    """多轮记忆：第二轮必须带上第一轮，历史条数受 `history_limit` 约束。

    这是产品核心行为，此前**零覆盖**（`history_limit` 在 tests 下 0 命中）：
    把 `[*history, user_message]` 改成 `[user_message]`（助手失忆）、
    或删掉 `limit=history_limit`（上下文无限膨胀），都没有测试会变红。
    验证点只能落在「送进模型的 messages」上 —— 光看回复看不出来。
    """

    def _seed_history(self, db: Database) -> str:
        """直接经 store 写一段历史（不比绕 HTTP，读起来更清楚）。"""
        store = ConversationStore(db)
        conversation = store.create(title="老会话")
        store.append_many(
            conversation.id,
            [
                Message(role=Role.USER, content="第1问"),
                Message(role=Role.ASSISTANT, content="第1答"),
                Message(role=Role.USER, content="第2问"),
                Message(role=Role.ASSISTANT, content="第2答"),
            ],
        )
        return conversation.id

    def test_second_turn_carries_the_first_turn_over_http(self, db: Database) -> None:
        llm = ScriptedLLM()
        for client in _client(db, llm):
            first = _parse_sse(
                client.post("/api/assistant/stream", json={"message": "第一问"}).text
            )[-1]["conversation_id"]
            _parse_sse(
                client.post(
                    "/api/assistant/stream",
                    json={"message": "第二问", "conversation_id": first},
                ).text
            )

        # calls[0] / calls[1] 是第一轮的两跳（工具 + 结论）；calls[2] 是第二轮的第一跳
        sent = llm.calls[2]
        assert [m.role for m in sent] == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.USER]
        assert sent[1].content == "第一问"
        assert sent[2].content == "共 1 条岗位。"
        assert sent[3].content == "第二问"

    def test_second_turn_carries_history_on_the_one_shot_endpoint(self, db: Database) -> None:
        """两条交付路径共用同一套上下文组装 —— 不能只有流式那条记住历史。"""
        llm = ScriptedLLM()
        for client in _client(db, llm):
            first = client.post("/api/assistant/turn", json={"message": "第一问"}).json()[
                "conversation_id"
            ]
            client.post("/api/assistant/turn", json={"message": "第二问", "conversation_id": first})

        contents = [m.content for m in llm.calls[2]]
        assert "第一问" in contents
        assert contents[-1] == "第二问"

    def test_history_limit_trims_old_context(self, db: Database) -> None:
        """`history_limit` 必须真的生效 —— 否则上下文无限膨胀。"""
        conversation_id = self._seed_history(db)
        llm = ScriptedLLM()
        for client in _client(db, llm, history_limit=2):
            client.post(
                "/api/assistant/turn",
                json={"message": "新问题", "conversation_id": conversation_id},
            )

        sent = llm.calls[0]
        assert sent[0].role is Role.SYSTEM
        # 只保留**最近 2 条**历史 + 本次提问（不是全部 4 条）
        assert [m.content for m in sent[1:]] == ["第2问", "第2答", "新问题"]


class TestPartialAnswerIsNotPersisted:
    """流已吐出一部分后失败：半截内容不得当答案落库。

    网关用 SSE `data: {"error": ...}` 报错时 `parse_sse_lines` 抛
    `LLMError("stream_error")`（见 platform/llm/streaming.py）；这里钉住端点
    侧的处理：如实把它交给前端（error 事件），**不落库** —— 否则下次会把
    「半截回答」当成上一轮的结论再喂给模型。
    """

    def test_partial_then_error_is_reported_and_not_persisted(self, db: Database) -> None:
        llm = MidStreamFailureLLM()
        for client in _client(db, llm):
            events = _parse_sse(
                client.post("/api/assistant/stream", json={"message": "余额还有吗"}).text
            )

        assert [event["type"] for event in events] == ["text", "error"]
        assert events[0]["text"] == "半截回答"
        assert "stream_error" in events[1]["message"]
        assert "Insufficient Balance" in events[1]["message"]
        # 半截回答与失败那句都不落库
        assert db.conversations().list() == []
        assert llm.closed is True
