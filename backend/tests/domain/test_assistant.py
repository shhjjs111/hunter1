"""助手领域类型单元测试 —— 纯数据与序列化，无 IO。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
"""

from __future__ import annotations

import json

import pytest

from hunter1.domain.assistant import (
    Message,
    Role,
    ToolCall,
    ToolResult,
    parse_tool_calls,
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

    def test_assistant_message_needs_content_or_tool_calls(self) -> None:
        """助手消息不能既没正文又没工具调用 —— 那是一条会被上游拒掉的空回合。

        实测：把 `domain/assistant.py` 里这条守卫改成 `if False:`，domain 与
        assistant 两个套件的用例**全都照样绿** —— `role is not Role.ASSISTANT`
        那条（上面那条用例守的）与它是两个条件，只有前者有覆盖。
        """
        with pytest.raises(ValueError):
            Message(role=Role.ASSISTANT, content="")
        # 有工具调用时允许空正文（模型先要工具、下一轮再说话）—— 配对反例
        with_tool = Message(
            role=Role.ASSISTANT, content="", tool_calls=[ToolCall(id="c1", name="t", arguments={})]
        )
        assert with_tool.content == ""

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


class TestSyntheticCallIds:
    """模型没返回 id 时合成的 id 必须在**进程内唯一**。

    原先回退用的是下标（`call_0`）：一次对话可能有多轮工具调用，每轮都是新的
    模型响应、下标每轮从 0 重来 —— 于是同一份 messages 里会出现重复的
    tool_call_id，而 OpenAI 协议要求 tool 消息的 tool_call_id 对应某条 tool_call。
    """

    def test_keeps_model_supplied_id(self) -> None:
        calls = parse_tool_calls([{"id": "real-id", "function": {"name": "f", "arguments": "{}"}}])
        assert calls[0].id == "real-id"

    def test_synthesized_ids_do_not_collide_across_responses(self) -> None:
        payload = [{"function": {"name": "search_jobs", "arguments": "{}"}}]
        first = parse_tool_calls(payload)
        second = parse_tool_calls(payload)
        assert first[0].id != second[0].id, "两轮响应的合成 id 不能相同"

    def test_ids_unique_within_one_response(self) -> None:
        payload = [
            {"function": {"name": "a", "arguments": "{}"}},
            {"function": {"name": "b", "arguments": "{}"}},
        ]
        ids = [call.id for call in parse_tool_calls(payload)]
        assert len(set(ids)) == len(ids)

    def test_ids_are_not_recycled_by_a_restart(self) -> None:
        """两次「进程启动」发出的第一个 id 必须不同。

        进程内计数器每重启一次就从 1 重来 —— 会和库里已持久化的 `call_auto_1`
        撞号（触发条件：网关不返 id + 重启 + 同一会话）。这里用**两个真子进程**
        验证：计数器实现下它们必然相同；随机实现下必然不同。
        """
        first = _id_from_fresh_process()
        second = _id_from_fresh_process()
        assert first.startswith("call_auto_")
        assert first != second, "重启后复用了同一个合成 id（进程内计数器？）"


def _id_from_fresh_process() -> str:
    """在一个全新的解释器里取一个合成 id（模拟「重启」）。"""
    import subprocess
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    script = (
        f"import sys; sys.path.insert(0, r'{src}');"
        "from hunter1.domain.assistant import synthetic_call_id;"
        "print(synthetic_call_id())"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        # 显式 utf-8：text=True 不给 encoding 时按宿主 locale（Windows cp936）解码，
        # 遇到非 ASCII 字节即让 reader 线程崩、stdout 变 None。此处输出是 ASCII，
        # 但同族一并修，避免将来脚本内容变化后本机红、CI 绿。
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return completed.stdout.strip()
