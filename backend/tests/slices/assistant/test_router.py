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
from pydantic import ValidationError

from hunter1.domain.assistant import Message, Role, ToolCall
from hunter1.domain.llm import LLMError, LLMResponse, StreamComplete, TextDelta
from hunter1.platform.db import Database
from hunter1.slices.assistant.job_tools import build_tools
from hunter1.slices.assistant.router import FALLBACK_REPLY, build_router
from hunter1.slices.assistant.schemas import MAX_MESSAGE_CHARS, StreamRequest
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

    def __init__(
        self,
        *,
        reply: str = "共 1 条岗位。",
        boom: bool = False,
        finish_reason: str | None = None,
        deltas: tuple[str, ...] = ("共 ", "1 条", "岗位。"),
    ) -> None:
        self.reply = reply
        self.boom = boom
        self.finish_reason = finish_reason
        #: 流式路径吐出的增量。默认与 `reply` 一致；给空元组就能造出
        #: 「一个增量都没有」的空回复（`done` 事件的 reply 与落库必须仍然一致）。
        self.deltas = deltas
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
        return LLMResponse(content=self.reply, model="fake", finish_reason=self.finish_reason)

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
        for chunk in self.deltas:
            yield TextDelta(chunk)
        yield StreamComplete(content=self.reply, model="fake", finish_reason=self.finish_reason)


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
            payload = client.get("/api/assistant/conversations").json()
            assert payload["items"] == []
            assert payload["total"] == 0
            assert payload["has_next"] is False

    def test_list_after_turn(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            client.post("/api/assistant/turn", json={"message": "有哪些产品岗？"})
            payload = client.get("/api/assistant/conversations").json()
            items = payload["items"]
            assert len(items) == 1
            assert "有哪些产品岗" in items[0]["title"]
            assert payload["total"] == 1 and payload["has_next"] is False

    def test_default_page_keeps_the_previous_behaviour(self, db: Database) -> None:
        """默认页大小 = 原来的固定上限 —— ≤50 个会话时首页与改动前**逐条一致**。"""
        import hunter1.slices.assistant.router as router_module

        for client in _client(db, ScriptedLLM()):
            payload = client.get("/api/assistant/conversations").json()
            assert payload["page_size"] == router_module.MAX_PAGE_SIZE == 50

    def test_pagination_reaches_the_older_conversations(self, db: Database) -> None:
        """会话**可以翻页**：第 N 页够得着更旧的会话。

        这条是原先缺的功能。之前只有截断信号（`total` / `has_more`）而没有翻页入口
        —— 第 51 个起的会话不只是「不可见」，而是**完全无法触达**：界面能说「还有
        更多」，却没有任何办法把它取出来。
        """
        for client in _client(db, ScriptedLLM()):
            for index in range(3):
                client.post("/api/assistant/turn", json={"message": f"第 {index} 问"})

            first = client.get("/api/assistant/conversations", params={"page_size": 2}).json()
            assert (first["page"], first["page_size"]) == (1, 2)
            assert len(first["items"]) == 2
            assert first["total"] == 3
            assert first["has_next"] is True, "还有更旧的，必须说「有下一页」"

            second = client.get(
                "/api/assistant/conversations", params={"page_size": 2, "page": 2}
            ).json()
            assert len(second["items"]) == 1
            assert second["has_next"] is False, "末页不该再说有下一页"
            # 第二页给的是**另外**的会话（不是第一页的重复），两页合起来才是全部
            assert {item["id"] for item in second["items"]}.isdisjoint(
                {item["id"] for item in first["items"]}
            )
            assert len(first["items"]) + len(second["items"]) == first["total"]

    def test_page_size_upper_bound_is_enforced(self, db: Database) -> None:
        """`page_size` 有上界（没有「一次取全部」的口子），越界由 **422** 拒绝。"""
        import hunter1.slices.assistant.router as router_module

        for client in _client(db, ScriptedLLM()):
            over = router_module.MAX_PAGE_SIZE + 1
            assert (
                client.get("/api/assistant/conversations", params={"page_size": over}).status_code
                == 422
            )
            assert client.get("/api/assistant/conversations", params={"page": 0}).status_code == 422

    def test_page_beyond_the_end_is_empty_not_an_error(self, db: Database) -> None:
        """翻过头不是错误：空页 + `has_next=False`（界面据此禁用「下一页」）。"""
        for client in _client(db, ScriptedLLM()):
            client.post("/api/assistant/turn", json={"message": "只有一条"})
            payload = client.get(
                "/api/assistant/conversations", params={"page": 5, "page_size": 2}
            ).json()
            assert payload["items"] == []
            assert payload["total"] == 1
            assert payload["has_next"] is False

    def test_messages_of_unknown_conversation_is_404(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            assert client.get("/api/assistant/conversations/nope").status_code == 404

    def test_messages_after_turn(self, db: Database) -> None:
        for client in _client(db, ScriptedLLM()):
            client.post("/api/assistant/turn", json={"message": "有哪些产品岗？"})
            conversation_id = client.get("/api/assistant/conversations").json()["items"][0]["id"]
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

    def test_overlong_message_is_rejected(self, db: Database) -> None:
        """`message` 有长度上限。

        它送往 LLM，且原先**只有** `min_length=1` —— 全仓别的大输入都设了闸门，
        唯独这个直通模型的入口没有：请求体、提示词长度和上游费用一起被放大。
        """
        too_long = "字" * (MAX_MESSAGE_CHARS + 1)
        for client in _client(db, ScriptedLLM()):
            assert client.post("/api/assistant/turn", json={"message": too_long}).status_code == 422
            assert (
                client.post("/api/assistant/stream", json={"message": too_long}).status_code == 422
            )

    def test_message_length_boundary(self) -> None:
        """边界本身要放行：恰好上限可以，多一个字符不行。

        直接在模型层测，不经过路由 —— 免得为了一次边界断言去消耗假 LLM 的脚本。
        """
        assert StreamRequest(message="字" * MAX_MESSAGE_CHARS).message == "字" * MAX_MESSAGE_CHARS
        with pytest.raises(ValidationError):
            StreamRequest(message="字" * (MAX_MESSAGE_CHARS + 1))


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


class TestUnknownConversationOnWritePaths:
    """写端点对「传了 conversation_id 却查不到」必须 404，与读端点同一语义。

    原先两个写端点静默新建会话（history 为空），而读端点对同一资源返回 404 ——
    同一资源「不存在」在读写两面语义相反。后果：拼错或已删除的 id 永不暴露，
    用户以为在续一段对话，实际上下文已丢且无从察觉。
    """

    def test_turn_rejects_unknown_conversation(self, db: Database) -> None:
        llm = ScriptedLLM()
        for client in _client(db, llm):
            response = client.post(
                "/api/assistant/turn", json={"message": "接着聊", "conversation_id": "nope"}
            )
            assert response.status_code == 404
            assert "会话不存在" in response.json()["detail"]
        # 拒绝请求不该落任何库、也不该白花一次模型调用
        assert db.conversations().list() == []

    def test_stream_rejects_unknown_conversation_before_opening_the_stream(
        self, db: Database
    ) -> None:
        """开流**之前**就该 404 —— 前端拿到的是一条标准 404 JSON，不是 SSE error 事件。

        这是「一次性端点与流式端点对同一契约给同一状态码」的前提：放进 generator
        里就晚了（响应已开始，只能作为 error 事件发出），前端得写两套判断。
        """
        llm = ScriptedLLM()
        for client in _client(db, llm):
            response = client.post(
                "/api/assistant/stream", json={"message": "接着聊", "conversation_id": "nope"}
            )
            assert response.status_code == 404
            assert response.headers["content-type"].startswith("application/json")
            assert "会话不存在" in response.json()["detail"]

    def test_turn_still_works_with_a_known_conversation(self, db: Database) -> None:
        """回归护栏：合法 id 必须照常续接，别把正常路径一起挡掉。"""
        for client in _client(db, ScriptedLLM()):
            first = client.post("/api/assistant/turn", json={"message": "第一句"})
            conversation_id = first.json()["conversation_id"]
            second = client.post(
                "/api/assistant/turn",
                json={"message": "第二句", "conversation_id": conversation_id},
            )
            assert second.status_code == 200
            assert second.json()["conversation_id"] == conversation_id


class TestLengthTruncatedAnswerIsAnnounced:
    """被 token 上限截断的回答必须显式告知用户。

    `finish_reason == "length"` 说明回答是**半截的**，而它在外观上与完整回答无异 ——
    不说出来就是「静默截断」（用户以为助手说完了）。

    注：流式路径的回复取 `spoken or completion.content`，即**增量拼起来的文本**
    （ScriptedLLM 固定吐「共 」「1 条」「岗位。」），所以断言用拼好的串而不是 `reply`。
    """

    STREAMED = "共 1 条岗位。"

    def test_stream_appends_a_notice_when_truncated_by_length(self, db: Database) -> None:
        llm = ScriptedLLM(finish_reason="length")
        for client in _client(db, llm):
            events = _parse_sse(client.post("/api/assistant/stream", json={"message": "讲讲"}).text)
        done = next(event for event in events if event["type"] == "done")
        assert self.STREAMED in done["reply"]
        assert "长度上限" in done["reply"]

    def test_stream_stays_quiet_when_finish_reason_is_stop(self, db: Database) -> None:
        llm = ScriptedLLM(finish_reason="stop")
        for client in _client(db, llm):
            events = _parse_sse(client.post("/api/assistant/stream", json={"message": "讲讲"}).text)
        done = next(event for event in events if event["type"] == "done")
        assert done["reply"] == self.STREAMED

    def test_missing_finish_reason_does_not_fake_a_notice(self, db: Database) -> None:
        """厂商没给结束原因时不能瞎提示（大量兼容网关就是不回这一项）。"""
        llm = ScriptedLLM()
        for client in _client(db, llm):
            events = _parse_sse(client.post("/api/assistant/stream", json={"message": "讲讲"}).text)
        done = next(event for event in events if event["type"] == "done")
        assert done["reply"] == self.STREAMED
        assert "长度上限" not in done["reply"]

    # ---- 同一条规矩的另一面：一次性端点 ----
    #
    # `/assistant/turn` 与 `/assistant/stream` 是**同一资源**的两个面（见 router 的
    # 注释）。只提示流式那一边，非流式的调用方（脚本 / 无 JS 的回退路径）就会把
    # 半截回答当完整答案落库 —— 而两者在外观上无从分辨。

    def test_one_shot_turn_appends_the_same_notice(self, db: Database) -> None:
        llm = ScriptedLLM(finish_reason="length")
        for client in _client(db, llm):
            reply = client.post("/api/assistant/turn", json={"message": "讲讲"}).json()["reply"]
            assert self.STREAMED in reply
            assert "长度上限" in reply
            # 两个面给的是同一段文字，不是各写一套
            events = _parse_sse(client.post("/api/assistant/stream", json={"message": "讲讲"}).text)
            done = next(event for event in events if event["type"] == "done")
            assert reply == done["reply"]

    def test_blank_reply_is_identical_in_the_event_and_in_the_store(self, db: Database) -> None:
        """空回复时，`done` 事件与落库必须是**同一段文字**。

        兜底文案原先只在 `_persist` 里加：库里存 `FALLBACK_REPLY`，事件里报原始空串。
        界面于是显示这条回答缺了，重新打开这条会话却又看得见内容 —— 两个面各自都
        「对」，合起来是矛盾。上面那条用例只覆盖了非空回复（默认脚本会吐增量），
        所以漏掉了它。这里用 `deltas=()` 造「一个增量都没有」的空回复。
        """
        llm = ScriptedLLM(reply="", deltas=())
        for client in _client(db, llm):
            events = _parse_sse(client.post("/api/assistant/stream", json={"message": "讲讲"}).text)
            done = next(event for event in events if event["type"] == "done")
            stored = client.get(f"/api/assistant/conversations/{done['conversation_id']}").json()
            assert stored[-1]["content"] == FALLBACK_REPLY, "落库这一侧应当走兜底文案"
            assert done["reply"] == stored[-1]["content"], (
                "done 事件报的回复与落库内容不一致：空回复时事件给的是原始空串"
            )

    def test_one_shot_turn_stays_quiet_when_finish_reason_is_stop(self, db: Database) -> None:
        llm = ScriptedLLM(finish_reason="stop")
        for client in _client(db, llm):
            reply = client.post("/api/assistant/turn", json={"message": "讲讲"}).json()["reply"]
            assert reply == self.STREAMED

    def test_one_shot_turn_stays_quiet_when_finish_reason_is_missing(self, db: Database) -> None:
        """厂商不回这一项时不能瞎提示（与流式那条配对）。"""
        llm = ScriptedLLM()
        for client in _client(db, llm):
            reply = client.post("/api/assistant/turn", json={"message": "讲讲"}).json()["reply"]
            assert reply == self.STREAMED
            assert "长度上限" not in reply
