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

from hunter1.domain.assistant import Message, ToolCall
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
    """按脚本作答：第一轮请求工具，第二轮给出结论。"""

    def __init__(self, *, reply: str = "共 1 条岗位。", boom: bool = False) -> None:
        self.reply = reply
        self.boom = boom
        self.round = 0

    # 一次性路径
    def complete_with_tools(self, *, messages: list[Message], **_kw: Any) -> LLMResponse:
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


def _client(db: Database, llm: ScriptedLLM) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(
        build_router(
            store=ConversationStore(db),
            llm_factory=lambda: llm,
            tools=build_tools(jobs=db.jobs(), applications=db.applications()),
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

    def test_model_failure_returns_502_and_creates_no_conversation(self, db: Database) -> None:
        """失败不留空会话 —— 否则下次会把失败那句当上下文再问一遍。"""
        for client in _client(db, ScriptedLLM(boom=True)):
            response = client.post("/api/assistant/turn", json={"message": "你好"})
            assert response.status_code == 502
            assert "upstream_failed" in response.json()["detail"]
            assert db.conversations().list() == []

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
