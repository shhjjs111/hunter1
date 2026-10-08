"""求职助手的领域类型 —— 消息、工具调用、工具结果。纯数据，无 IO。

这些类型是「助手」与「模型」「工具」之间的共同语言，放在 domain 层：
应用层的 agent 循环、基础设施层的协议实现都依赖它们，但都不拥有它们。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

# 给「模型没返回 id」的工具调用发号。**不能用下标**：一次对话可能有多轮工具调用，
# 每轮都是一次新的模型响应、下标每轮从 0 重来 —— 于是同一份 messages 里会出现
# 重复的 tool_call_id，而 OpenAI 协议要求 tool 消息的 tool_call_id 对应某条
# tool_call，重复即歧义。用随机串保证**跨进程、跨重启**都不撞号（见
# `synthetic_call_id` —— 进程内计数器会在重启后与库里已有的 id 重号）。


def synthetic_call_id() -> str:
    """生成一个工具调用 id（模型未返回 id 时的回退）。

    用随机串而不是进程内计数器：计数器只保证**进程内**唯一 —— 重启后从 1 重来，
    会和库里已持久化的 `call_auto_1` 撞号。而 tool_call_id 必须在一条 messages
    序列里无歧义（服务器可能按它配对），撞号意味着两轮不同的工具调用同名。
    """
    return f"call_auto_{uuid4().hex[:12]}"


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """模型请求的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.arguments, dict):
            raise ValueError("tool call arguments must be an object")


@dataclass
class ToolResult:
    """一次工具执行的结果。失败用 `error` 显式表达，不混进 content。"""

    call_id: str
    name: str
    content: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def as_message(self) -> Message:
        """转成回灌给模型的 tool 消息。失败时把错误文本也给它 —— 让模型能纠正。"""
        text = self.content if self.ok else f"[工具错误] {self.error}"
        return Message(role=Role.TOOL, content=text or "(空结果)", tool_call_id=self.call_id)


@dataclass
class Message:
    """一条对话消息。"""

    role: Role
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.role is not Role.ASSISTANT and not (self.content or "").strip():
            raise ValueError(f"{self.role} message must have non-blank content")
        if self.role is Role.TOOL and not self.tool_call_id:
            raise ValueError("tool message must carry tool_call_id")
        if self.role is Role.ASSISTANT and not (self.content or "").strip() and not self.tool_calls:
            raise ValueError("assistant message must have content or tool_calls")

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """把消息序列化为 OpenAI `/chat/completions` 的 messages 数组。"""
    payload: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": str(message.role), "content": message.content}
        if message.has_tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        payload.append(item)
    return payload


def parse_tool_calls(raw: list[dict[str, Any]]) -> list[ToolCall]:
    """从 OpenAI 响应的 `tool_calls` 解析出领域对象（容忍参数是 JSON 字符串）。"""
    calls: list[ToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                parsed = {"__raw__": arguments}
        elif isinstance(arguments, dict):
            parsed = arguments
        else:
            parsed = {}
        call_id = item.get("id") or synthetic_call_id()
        calls.append(ToolCall(id=str(call_id), name=name.strip(), arguments=parsed))
    return calls


__all__ = [
    "Message",
    "Role",
    "ToolCall",
    "ToolResult",
    "parse_tool_calls",
    "synthetic_call_id",
    "to_openai_messages",
]
