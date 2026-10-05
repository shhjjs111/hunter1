"""助手领域类型单元测试 —— 纯数据与序列化，无 IO。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

import json

import pytest

from hunter1.domain.assistant import (
    Message,
    Role,
    ToolCall,
    ToolResult,
    to_openai_messages,
)


class TestMessage:
    def test_user_message(self) -> None:
        msg = Message(role=Role.USER, content="帮我看看有什么岗位")
        assert msg.role == "user"
        assert msg.content == "帮我看看有什么岗位"

    def test_blank_content_rejected(self) -> None:
        with pytest.raises(ValueError):
            Message(role=Role.USER, content="   ")

    def test_tool_message_carries_call_id(self) -> None:
        msg = Message(role=Role.TOOL, content="结果", tool_call_id="call_1")
        assert msg.tool_call_id == "call_1"

    def test_tool_message_requires_call_id(self) -> None:
        """工具结果必须能对上是哪次调用发出来的。"""
        with pytest.raises(ValueError):
            Message(role=Role.TOOL, content="结果")


class TestToolCall:
    def test_construction(self) -> None:
        call = ToolCall(id="call_1", name="search_jobs", arguments={"keyword": "产品"})
        assert call.name == "search_jobs"
        assert call.arguments == {"keyword": "产品"}

    def test_arguments_must_be_object(self) -> None:
        with pytest.raises(ValueError):
            ToolCall(id="c", name="t", arguments="不是对象")  # type: ignore[arg-type]


class TestToolResult:
    def test_success(self) -> None:
        result = ToolResult(call_id="call_1", name="search_jobs", content="找到 3 条")
        assert result.content == "找到 3 条"
        assert result.error is None

    def test_error_is_explicit(self) -> None:
        """工具失败必须显式带 error —— 让模型能看见失败并调整，而不是静默。"""
        result = ToolResult(call_id="c", name="t", content="", error="参数 keyword 缺失")
        assert result.error is not None


class TestSerializeForOpenAI:
    def test_plain_messages(self) -> None:
        messages = [
            Message(role=Role.SYSTEM, content="你是求职助手"),
            Message(role=Role.USER, content="你好"),
        ]
        payload = to_openai_messages(messages)
        assert payload == [
            {"role": "system", "content": "你是求职助手"},
            {"role": "user", "content": "你好"},
        ]

    def test_assistant_message_with_tool_calls(self) -> None:
        msg = Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[ToolCall(id="call_1", name="search_jobs", arguments={"q": "产品"})],
        )
        payload = to_openai_messages([msg])
        assert payload[0]["role"] == "assistant"
        assert payload[0]["tool_calls"][0]["id"] == "call_1"
        assert payload[0]["tool_calls"][0]["type"] == "function"
        assert payload[0]["tool_calls"][0]["function"]["name"] == "search_jobs"
        # 参数必须是 JSON 字符串（OpenAI 协议要求）
        args = json.loads(payload[0]["tool_calls"][0]["function"]["arguments"])
        assert args == {"q": "产品"}

    def test_tool_result_message_shape(self) -> None:
        msg = Message(role=Role.TOOL, content="找到 3 条", tool_call_id="call_1")
        payload = to_openai_messages([msg])
        assert payload[0] == {"role": "tool", "content": "找到 3 条", "tool_call_id": "call_1"}

    def test_assistant_text_only_has_no_tool_calls_key(self) -> None:
        msg = Message(role=Role.ASSISTANT, content="好的")
        payload = to_openai_messages([msg])
        assert "tool_calls" not in payload[0]
