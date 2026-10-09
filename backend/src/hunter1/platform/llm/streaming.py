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

**例外：`error` 帧不宽容。** 兼容网关在余额不足 / 风控 / 过载时用
`data: {"error": {...}}` 报告失败（正文里没有 `choices`）。把它当作「一条无关的
数据」跳过，整条流就会以「空回复」**正常收尾**：助手把失败当答案落库，错误信息
全部丢失；若失败发生在已吐出一段文本之后，半截内容还会被当成最终回答展示。
非流式路径对同一失败会抛 `response_invalid` —— 两条路径的行为必须一致，
所以这里抛 `LLMError("stream_error")`（由消费方按「上游失败」处理）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from hunter1.domain.assistant import ToolCall, synthetic_call_id
from hunter1.domain.llm import LLMError, StreamComplete, TextDelta

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

    **一个事件的数据可以分在多行 `data:` 上**（SSE 协议允许，有的网关就这么发）：
    同一个事件里的多行要先用 `\\n` 拼成一个载荷再解析。逐行独立 `json.loads` 会让
    拆行发的事件「两行各自失败」→ 被当坏数据跳过 → 内容静默丢失、流以「空回复
    正常收尾」。空行是事件分隔符，遇到它才把攒下的行交去解析。
    """
    text_parts: list[str] = []
    slots: dict[int, _ToolSlot] = {}
    # 「当前槽」= 最近一次出现过的槽位，**不是** `max(slots)`。
    # 反例：先来 index=5 的调用，再来 index=0 的调用，最后来一个不带 index 的
    # 续片 —— 用 max 会把续片并进 5 号槽，两个调用的 arguments 拼成 `{}{}`。
    current_index: int | None = None
    model = default_model
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None

    def handle(pieces: list[str]) -> Iterator[TextDelta]:
        """解析一个完整事件（data 行已按协议拼好）。"""
        nonlocal current_index, input_tokens, model, output_tokens, finish_reason
        for payload_text in _event_payloads(pieces):
            try:
                payload = json.loads(payload_text)
            except ValueError:
                continue  # 坏数据不该毁掉整条流
            if not isinstance(payload, dict):
                continue

            error = payload.get("error")
            if error is not None:
                # 见模块 docstring：`error` 帧是**失败**，不是「无关数据」——
                # 跳过它会让整条流以空回复正常收尾（助手把失败当答案落库）。
                raise LLMError("stream_error", _error_text(error))

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
                reason = choice.get("finish_reason")
                if isinstance(reason, str) and reason.strip():
                    # 记下厂商给的结束原因（`length` = 被 token 上限截断）。留一个
                    # 非空判定：有的网关每个 chunk 都带 `finish_reason: null`。
                    finish_reason = reason.strip()
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
                            current_index = _absorb(slots, fragment, current_index)

    buffer: list[str] = []
    for raw in lines:
        line = raw.strip()
        # 空行是事件分隔符；`:` 开头是注释（keep-alive ping）
        if not line:
            if buffer:
                yield from handle(buffer)
                buffer = []
            continue
        if line.startswith(":"):
            continue
        if not line.startswith(_DATA_PREFIX):
            continue
        piece = line[len(_DATA_PREFIX) :].strip()
        if not piece:
            continue
        if piece == _DONE_MARKER:
            # 先把攒下的事件解析掉，再收尾 —— 否则同一事件里 `[DONE]` 前的内容会丢。
            if buffer:
                yield from handle(buffer)
                buffer = []
            break
        buffer.append(piece)

    if buffer:
        yield from handle(buffer)

    yield StreamComplete(
        content="".join(text_parts),
        model=model,
        tool_calls=_finalize(slots),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        finish_reason=finish_reason,
    )


def _event_payloads(pieces: list[str]) -> list[str]:
    """一个 SSE 事件的 data 行 → 若干可解析的载荷。

    协议规定同一事件的多行 data 用 `\\n` 拼成一个载荷，所以先按协议拼（有的网关
    把整个 JSON 拆成多行发）。但也见过**不合规**的实现：没有空行分隔、连发两个
    完整 JSON —— 那种情况拼起来反而坏掉，于是退回「逐行各自解析」。
    """
    if len(pieces) == 1:
        return pieces
    joined = "\n".join(pieces)
    try:
        json.loads(joined)
    except ValueError:
        return pieces
    return [joined]


def _absorb(
    slots: dict[int, _ToolSlot], fragment: dict[str, object], current_index: int | None
) -> int:
    """把一片工具调用增量并进对应槽位；返回「当前槽」的下标。"""
    index = _slot_index(slots, fragment, current_index)
    slot = slots.setdefault(index, _ToolSlot())

    # id / name 只在第一片出现；重复出现时以先到的为准（有的实现会重发）。
    call_id = fragment.get("id")
    if isinstance(call_id, str) and call_id and not slot.id:
        slot.id = call_id

    function = fragment.get("function")
    if not isinstance(function, dict):
        return index
    name = function.get("name")
    if isinstance(name, str) and name and not slot.name:
        slot.name = name
    arguments = function.get("arguments")
    if isinstance(arguments, str) and arguments:
        slot.arguments += arguments
    return index


def _slot_index(
    slots: dict[int, _ToolSlot], fragment: dict[str, object], current_index: int | None
) -> int:
    """决定这一片片段该归哪个槽位（返回下标）。

    带 `index` 时按它归位（OpenAI 系）。

    不带 `index` 时**既不能一律并进 0 号槽，也不能并进 `max(slots)`**：

    - 有的兼容网关把每次调用当作一个完整片段发出（没有 `index`）—— 全并进 0 号槽
      会把两个调用合成一个：第二个调用消失，arguments 拼成
      `{"__raw__": "{\"city\":}{}"}` 这类垃圾；
    - 「当前调用」是**最近一次出现过的**那个槽（`current_index`），不是下标最大的
      那个。反例：先来 index=5 的调用、再来 index=0 的调用，最后来一个不带 index
      的续片 —— 用 `max(slots)` 会把它并进 5 号槽，两个调用的 arguments 拼成 `{}{}`。

    没有 `index` 时的判据是「这片片段里的身份信息是不是**另一个**调用」：
    带 id 且与当前槽不同、或带 name 且与当前槽不同 → 新调用；否则是续片。

    刻意不把「name 相同」当新调用：`id`/`name` 有的实现会重发，按名字判新会把
    一次调用劈成两半。「连续两次同名工具调用、且都不带 id」这种形态仍会合并 ——
    数据上分辨不出来，这里选择偏向「不劈」。
    """
    index = fragment.get("index")
    if isinstance(index, int):
        return index
    if current_index is None or current_index not in slots:
        # 还没见过任何调用 —— 落到 0 号槽
        return 0

    current = slots[current_index]
    if _starts_new_call(current, fragment):
        return max(slots) + 1
    return current_index


def _starts_new_call(current: _ToolSlot, fragment: dict[str, object]) -> bool:
    """无 `index` 的片段是在开启一次新调用，还是当前调用的续片？"""
    call_id = fragment.get("id")
    if isinstance(call_id, str) and call_id and current.id and call_id != current.id:
        return True
    function = fragment.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name and current.name and name != current.name:
            return True
    return False


def _finalize(slots: dict[int, _ToolSlot]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index in sorted(slots):
        slot = slots[index]
        if not slot.name:
            continue  # 没有名字的调用没有意义
        calls.append(
            ToolCall(
                id=slot.id or synthetic_call_id(),
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


def _error_text(error: object) -> str:
    """把 `error` 帧的内容压成一句可读的失败原因。

    形状各家不一：OpenAI 系是 `{"message": ..., "type": ...}`，有的网关直接给
    字符串，个别给 `{"detail": ...}`。取不到已知字段就退回原样的 JSON 片段 ——
    宁可难看也不要空着：这行文本是用户唯一能看到的失败线索。
    """
    if isinstance(error, dict):
        for key in ("message", "detail", "msg", "type"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
        return json.dumps(error, ensure_ascii=False)[:200]
    return str(error).strip()[:200]


__all__ = ["parse_sse_lines"]
