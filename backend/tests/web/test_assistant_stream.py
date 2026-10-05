"""助手流式端点测试 —— 离线（假流式模型 + 真 SQLite）。

流式端点与普通 POST 有一处根本差别：**响应一旦开始就没法再重定向**。
所以「出错」不能再靠 URL 上的 error 参数，必须走事件流里的一条 `error` 事件。
这一条如果漏了，用户看到的就是「点了发送，页面毫无反应」。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from hunter1.domain.assistant import ToolCall
from hunter1.domain.llm import StreamComplete, TextDelta
from tests.web.helpers import configure_llm, parse_sse


def _stream(client: TestClient, **data: str) -> tuple[Any, list[dict[str, Any]]]:
    with client.stream("POST", "/assistant/stream", data=data) as response:
        body = "".join(response.iter_text())
        return response, parse_sse(body)


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [event["type"] for event in events]


class TestTextStreaming:
    def test_emits_text_then_done(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        configure_llm(db)
        _response, events = _stream(client, message="有哪些岗位？")
        assert _types(events)[-1] == "done"
        assert "text" in _types(events)

    def test_text_events_reassemble_to_the_reply(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        configure_llm(db)
        _response, events = _stream(client, message="你好")
        streamed = "".join(e["text"] for e in events if e["type"] == "text")
        assert streamed == llm.reply
        assert events[-1]["reply"] == llm.reply

    def test_uses_the_streaming_path(self, app_env) -> None:  # type: ignore[no-untyped-def]
        """确认走的真是流式 —— 否则「逐字输出」是假的。"""
        client, db, llm = app_env
        configure_llm(db)
        _stream(client, message="你好")
        assert llm.stream_calls == 1

    def test_content_type_is_event_stream(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        configure_llm(db)
        response, _events = _stream(client, message="你好")
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_history_is_passed_to_the_model(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        configure_llm(db)
        _response, first = _stream(client, message="第一问")
        cid = first[-1]["conversation_id"]
        _stream(client, message="第二问", conversation_id=cid)
        # 第二轮应带上第一轮的历史：system + user + assistant + user
        assert len(llm.seen_messages) == 4
        assert llm.seen_messages[-1].content == "第二问"


class TestPersistence:
    def test_done_carries_conversation_id_and_persists(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        configure_llm(db)
        _response, events = _stream(client, message="有哪些岗位？")
        conversation_id = events[-1]["conversation_id"]
        assert conversation_id

        messages = db.conversations().messages(conversation_id)
        assert [m.role.value for m in messages] == ["user", "assistant"]
        assert messages[0].content == "有哪些岗位？"

    def test_reuses_existing_conversation(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        configure_llm(db)
        _response, first = _stream(client, message="第一问")
        cid = first[-1]["conversation_id"]

        _response, second = _stream(client, message="第二问", conversation_id=cid)
        assert second[-1]["conversation_id"] == cid
        assert len(db.conversations().list()) == 1
        assert len(db.conversations().messages(cid)) == 4

    def test_model_failure_persists_nothing(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        configure_llm(db)
        llm.reply = None
        _response, events = _stream(client, message="你好")
        assert _types(events) == ["error"]
        # 失败不留空会话，也不留半截对话
        assert db.conversations().list() == []


class TestToolEvents:
    def _tool_script(self) -> list[list[Any]]:
        return [
            [
                StreamComplete(
                    content="",
                    model="fake",
                    tool_calls=[
                        ToolCall(id="c1", name="search_jobs", arguments={"keyword": "产品"})
                    ],
                )
            ],
            [TextDelta("查到 2 条"), StreamComplete(content="查到 2 条", model="fake")],
        ]

    def test_tool_start_and_end_are_emitted(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        configure_llm(db)
        llm.script = self._tool_script()
        _response, events = _stream(client, message="找产品岗")
        kinds = _types(events)
        assert "tool_start" in kinds
        assert "tool_end" in kinds
        assert kinds.index("tool_start") < kinds.index("tool_end")

    def test_tool_events_carry_name_and_result(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        configure_llm(db)
        llm.script = self._tool_script()
        _response, events = _stream(client, message="找产品岗")
        started = next(e for e in events if e["type"] == "tool_start")
        ended = next(e for e in events if e["type"] == "tool_end")
        assert started["name"] == "search_jobs"
        assert started["arguments"] == {"keyword": "产品"}
        assert ended["ok"] is True
        assert "产品" in ended["content"]

    def test_tool_text_and_done_all_arrive(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        configure_llm(db)
        llm.script = self._tool_script()
        _response, events = _stream(client, message="找产品岗")
        assert events[-1]["type"] == "done"
        assert events[-1]["reply"] == "查到 2 条"


class TestFailurePaths:
    def test_unconfigured_model_yields_error_event(self, app_env) -> None:  # type: ignore[no-untyped-def]
        """没配模型时也要有明确反馈 —— 不能是一个空流。"""
        client, _db, _llm = app_env
        _response, events = _stream(client, message="你好")
        assert _types(events) == ["error"]
        assert "配置" in events[0]["message"]

    def test_blank_message_yields_error_event(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        configure_llm(db)
        _response, events = _stream(client, message="   ")
        assert _types(events) == ["error"]

    def test_failure_message_is_readable_and_bounded(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        configure_llm(db)
        llm.reply = None
        _response, events = _stream(client, message="你好")
        message = events[0]["message"]
        assert "模型不可用" in message
        assert len(message) <= 400  # 不把整段堆栈糊到界面上

    def test_degraded_flag_reaches_the_client(self, app_env) -> None:  # type: ignore[no-untyped-def]
        """厂商不支持流式时，界面要知道「本次不是逐字来的」。"""
        client, db, llm = app_env
        configure_llm(db)
        llm.script = [[StreamComplete(content="整段回答", model="m", degraded=True)]]
        _response, events = _stream(client, message="你好")
        assert events[-1]["degraded"] is True


class TestNonStreamingFallback:
    def test_plain_post_still_works(self, app_env) -> None:  # type: ignore[no-untyped-def]
        """没有 JS 时仍可用普通表单 —— 流式是渐进增强，不是唯一入口。"""
        client, db, _llm = app_env
        configure_llm(db)
        response = client.post("/assistant", data={"message": "你好"}, follow_redirects=True)
        assert response.status_code == 200
        assert "好的，共 2 条岗位。" in response.text
