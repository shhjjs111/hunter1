"""SSE 流的解析 —— 纯函数，不碰网络。

拆出来的理由：流式最难的从来不是 HTTP，而是**把碎片拼回结构**。
`arguments` 是若干字符串片段拼成的 JSON，`id`/`name` 只在第一片出现；
拼错的后果是「工具名对、参数丢失」，而且只在真机上才暴露。
做成纯函数就能拿真实片序离线回归。

宽容原则（与非流式的 `parse_tool_calls` 一致）：
- 一行坏 JSON 跳过，不毁掉整条流；
- 参数不是合法 JSON 时保留原文（`{"__raw__": ...}`），
  让模型在下一轮看到自己的错误并纠正；
- 缺工具名的残缺片段直接丢弃 —— 一次没有名字的调用没有意义。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from hunter1.domain.assistant import ToolCall
from hunter1.domain.llm import StreamComplete, TextDelta

_DATA_PREFIX = "data:"
_DONE_MARKER = "[DONE]"


@dataclass
class _ToolSlot:
    """一个工具调用的累积位（按 `index` 归位）。"""

    id: str = ""
    name: str = ""
    arguments: str = ""


def parse_sse_lines(
    lines: Iterable[str], *, default_model: str = ""
) -> Iterator[TextDelta | StreamComplete]:
    """把 SSE 行流解析成「若干 TextDelta + 一个 StreamComplete」。

    `default_model` 用于厂商没在流里回显模型名的情况（多数会回显）。
    """
    text_parts: list[str] = []
    slots: dict[int, _ToolSlot] = {}
    model = default_model
    input_tokens: int | None = None
    output_tokens: int | None = None

    for raw in lines:
        line = raw.strip()
        # 空行是事件分隔符；`:` 开头是注释（keep-alive ping）
        if not line or line.startswith(":"):
            continue
        if not line.startswith(_DATA_PREFIX):
            continue
        payload_text = line[len(_DATA_PREFIX) :].strip()
        if not payload_text:
            continue
        if payload_text == _DONE_MARKER:
            break

        try:
            payload = json.loads(payload_text)
        except ValueError:
            continue  # 单行坏数据不该毁掉整条流
        if not isinstance(payload, dict):
            continue

        reported = payload.get("model")
        if isinstance(reported, str) and reported.strip():
            model = reported.strip()

        usage = payload.get("usage")
        if isinstance(usage, dict):
            input_tokens = _token(usage.get("prompt_tokens"), input_tokens)
            output_tokens = _token(usage.get("completion_tokens"), output_tokens)

        choices = payload.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            content = delta.get("content")
            if isinstance(content, str) and content:
                text_parts.append(content)
                yield TextDelta(content)

            fragments = delta.get("tool_calls")
            if isinstance(fragments, list):
                for fragment in fragments:
                    if isinstance(fragment, dict):
                        _absorb(slots, fragment)

    yield StreamComplete(
        content="".join(text_parts),
        model=model,
        tool_calls=_finalize(slots),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _absorb(slots: dict[int, _ToolSlot], fragment: dict[str, object]) -> None:
    """把一片工具调用增量并进对应槽位。"""
    index = fragment.get("index")
    slot = slots.setdefault(index if isinstance(index, int) else 0, _ToolSlot())

    # id / name 只在第一片出现；重复出现时以先到的为准（有的实现会重发）。
    call_id = fragment.get("id")
    if isinstance(call_id, str) and call_id and not slot.id:
        slot.id = call_id

    function = fragment.get("function")
    if not isinstance(function, dict):
        return
    name = function.get("name")
    if isinstance(name, str) and name and not slot.name:
        slot.name = name
    arguments = function.get("arguments")
    if isinstance(arguments, str) and arguments:
        slot.arguments += arguments


def _finalize(slots: dict[int, _ToolSlot]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index in sorted(slots):
        slot = slots[index]
        if not slot.name:
            continue  # 没有名字的调用没有意义
        calls.append(
            ToolCall(
                id=slot.id or f"call_{index}",
                name=slot.name,
                arguments=_parse_arguments(slot.arguments),
            )
        )
    return calls


def _parse_arguments(raw: str) -> dict[str, object]:
    text = raw.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"__raw__": text}
    return parsed if isinstance(parsed, dict) else {"__raw__": parsed}


def _token(value: object, fallback: int | None) -> int | None:
    return value if isinstance(value, int) and value >= 0 else fallback


__all__ = ["parse_sse_lines"]
