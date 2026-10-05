"""统一 LLM provider 层的**流式**测试 —— 离线（httpx.MockTransport + 纯函数）。

流式有两件容易出错的事，这个文件主要盯着它们：

1. **工具调用是分片到达的**：`id`/`name` 只在第一片出现，`arguments` 是若干
   字符串片段拼起来的 JSON。拼错了就会变成"工具名对、参数丢失"，而且只有
   在真机上才炸。
2. **降级不能吞错误**：厂商不支持流式（400）时该退化为一次性输出；
   但鉴权失败（401）必须原样抛出 —— 否则用户会以为"模型就是没反应"。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hunter1.domain.llm import LLMError, StreamComplete, TextDelta
from hunter1.infrastructure.llm import OpenAICompatibleClient
from hunter1.infrastructure.llm.streaming import parse_sse_lines


def _client(handler: Any, **kwargs: Any) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url=kwargs.pop("base_url", "https://api.example.com/v1"),
        api_key=kwargs.pop("api_key", "sk-test"),
        model=kwargs.pop("model", "test-model"),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _wire(*payloads: str) -> list[str]:
    """把若干 `data:` 负载拼成 SSE 原始行（含空行分隔）。"""
    lines: list[str] = []
    for payload in payloads:
        lines.append(f"data: {payload}")
        lines.append("")
    return lines


def _delta(content: str | None) -> str:
    return json.dumps({"choices": [{"delta": {"content": content}}]})


def _sse(payloads: list[str]) -> httpx.Response:
    body = "".join(f"data: {payload}\n\n" for payload in payloads)
    return httpx.Response(
        200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"}
    )


def _texts(events: list[Any]) -> list[str]:
    return [event.text for event in events if isinstance(event, TextDelta)]


def _done(events: list[Any]) -> StreamComplete:
    finals = [event for event in events if isinstance(event, StreamComplete)]
    assert finals, "流结束时必须产出一个 StreamComplete"
    return finals[-1]


class TestParseSseLines:
    """纯解析：把 SSE 行变成事件序列。"""

    def _run(self, lines: list[str], *, model: str = "m") -> list[Any]:
        return list(parse_sse_lines(lines, default_model=model))

    def test_text_deltas_arrive_progressively(self) -> None:
        events = self._run(_wire(_delta("你"), _delta("好")))
        assert _texts(events) == ["你", "好"]

    def test_completion_carries_joined_content(self) -> None:
        events = self._run(_wire(_delta("你"), _delta("好"), _delta("！")))
        assert _done(events).content == "你好！"

    def test_done_marker_ends_the_stream(self) -> None:
        events = self._run(_wire(_delta("A"), "[DONE]", _delta("B")))
        assert _texts(events) == ["A"]
        assert _done(events).content == "A"

    def test_stream_without_done_marker_still_completes(self) -> None:
        """有些厂商不发 [DONE]，靠连接关闭结束 —— 不能因此丢结果。"""
        events = self._run(_wire(_delta("只有一片")))
        assert _done(events).content == "只有一片"

    def test_comment_and_blank_lines_are_ignored(self) -> None:
        lines = [": keep-alive", "", *_wire(_delta("好")), ": ping", ""]
        assert _texts(self._run(lines)) == ["好"]

    def test_malformed_json_line_is_skipped(self) -> None:
        """一行坏数据不该毁掉整条流 —— 真实链路里偶尔会出现。"""
        lines = ["data: {这不是 JSON}", *_wire(_delta("好"))]
        assert _texts(self._run(lines)) == ["好"]

    def test_null_content_delta_is_not_emitted(self) -> None:
        """工具调用片段的 `content` 是 null —— 不能产出空 TextDelta 打扰前端。"""
        events = self._run(_wire(_delta(None), _delta("好")))
        assert _texts(events) == ["好"]

    def test_tool_call_fragments_are_accumulated(self) -> None:
        """核心用例：参数分三片到达，要拼回一个完整的调用。"""
        lines = _wire(
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "search_jobs", "arguments": ""},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": '{"key'}}]
                            }
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": 'word": "产品"}'}}
                                ]
                            }
                        }
                    ]
                }
            ),
        )
        done = _done(self._run(lines))
        assert len(done.tool_calls) == 1
        call = done.tool_calls[0]
        assert call.id == "call_1"
        assert call.name == "search_jobs"
        assert call.arguments == {"keyword": "产品"}

    def test_multiple_tool_calls_are_kept_in_index_order(self) -> None:
        lines = _wire(
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 1,
                                        "id": "c2",
                                        "function": {"name": "second", "arguments": "{}"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {"name": "first", "arguments": "{}"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
        )
        done = _done(self._run(lines))
        assert [call.name for call in done.tool_calls] == ["first", "second"]

    def test_broken_tool_arguments_are_kept_raw(self) -> None:
        """参数不是合法 JSON 时保留原文，让模型在下一轮看到并纠正。"""
        lines = _wire(
            json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {"name": "x", "arguments": "not-json"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            )
        )
        done = _done(self._run(lines))
        assert done.tool_calls[0].arguments == {"__raw__": "not-json"}

    def test_tool_call_without_name_is_dropped(self) -> None:
        """只有参数、没有工具名的残缺片段不能变成一次调用。"""
        lines = _wire(
            json.dumps(
                {
                    "choices": [
                        {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}
                    ]
                }
            )
        )
        assert _done(self._run(lines)).tool_calls == []

    def test_usage_is_captured(self) -> None:
        lines = _wire(
            _delta("好"),
            json.dumps(
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                }
            ),
        )
        done = _done(self._run(lines))
        assert done.input_tokens == 11
        assert done.output_tokens == 7

    def test_model_is_captured_from_payload(self) -> None:
        lines = _wire(json.dumps({"model": "real-model", "choices": [{"delta": {"content": "x"}}]}))
        assert _done(self._run(lines, model="fallback")).model == "real-model"

    def test_model_falls_back_when_absent(self) -> None:
        assert _done(self._run(_wire(_delta("x")), model="fallback")).model == "fallback"

    def test_empty_stream_yields_empty_completion(self) -> None:
        events = self._run([])
        assert _texts(events) == []
        assert _done(events).content == ""
        assert _done(events).degraded is False


class TestStreamWithTools:
    """HTTP 层：请求形态、降级与错误处理。"""

    def test_request_marks_stream_true_and_sends_tools(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _sse([_delta("好"), "[DONE]"])

        list(
            _client(handler).stream_with_tools(
                messages=[], tools=[{"type": "function", "function": {"name": "f"}}]
            )
        )
        body = seen["body"]
        assert body["stream"] is True
        assert body["tools"][0]["function"]["name"] == "f"
        assert body["tool_choice"] == "auto"

    def test_content_is_joined_from_deltas(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _sse([_delta("你好"), _delta("，世界"), "[DONE]"])

        events = list(_client(handler).stream_with_tools(messages=[], tools=[]))
        assert _texts(events) == ["你好", "，世界"]
        assert _done(events).content == "你好，世界"
        assert _done(events).degraded is False

    def test_tool_calls_survive_the_stream(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _sse(
                [
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "id": "c1",
                                                "function": {
                                                    "name": "search_jobs",
                                                    "arguments": '{"keyword":',
                                                },
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ),
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "tool_calls": [
                                            {"index": 0, "function": {"arguments": '"产品"}'}}
                                        ]
                                    }
                                }
                            ]
                        }
                    ),
                    "[DONE]",
                ]
            )

        done = _done(list(_client(handler).stream_with_tools(messages=[], tools=[])))
        assert done.tool_calls[0].arguments == {"keyword": "产品"}

    def test_400_degrades_to_non_streaming(self) -> None:
        """厂商不支持流式时自动降级，并**如实标记** degraded。"""
        calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            if body.get("stream"):
                return httpx.Response(400, json={"error": "stream not supported"})
            return httpx.Response(
                200,
                json={
                    "model": "m",
                    "choices": [{"message": {"role": "assistant", "content": "整段回答"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                },
            )

        events = list(_client(handler).stream_with_tools(messages=[], tools=[]))
        assert _texts(events) == ["整段回答"]  # 仍然给前端一段文本
        done = _done(events)
        assert done.content == "整段回答"
        assert done.degraded is True
        assert done.input_tokens == 3
        assert len(calls) == 2  # 先流式、后降级

    def test_degraded_fallback_can_carry_tool_calls(self) -> None:
        """降级后仍需保留工具调用 —— 否则助手会变成"只会聊天"。"""

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("stream"):
                return httpx.Response(400, json={"error": "no stream"})
            return httpx.Response(
                200,
                json={
                    "model": "m",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {
                                            "name": "search_jobs",
                                            "arguments": '{"keyword": "产品"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                },
            )

        done = _done(list(_client(handler).stream_with_tools(messages=[], tools=[])))
        assert done.degraded is True
        assert done.tool_calls[0].name == "search_jobs"
        assert done.tool_calls[0].arguments == {"keyword": "产品"}

    def test_auth_error_is_not_swallowed_by_degradation(self) -> None:
        """401 降级也救不了 —— 必须原样抛出去，让用户看到真正的原因。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad key"})

        with pytest.raises(LLMError) as excinfo:
            list(_client(handler).stream_with_tools(messages=[], tools=[]))
        assert excinfo.value.code == "http_401"

    def test_transport_error_degrades_to_non_streaming(self) -> None:
        """连接阶段就失败 → 试一次非流式（那边有重试），而不是直接放弃。"""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("streaming broke")
            return httpx.Response(
                200,
                json={"model": "m", "choices": [{"message": {"content": "fallback"}}]},
            )

        done = _done(list(_client(handler).stream_with_tools(messages=[], tools=[])))
        assert done.content == "fallback"
        assert done.degraded is True

    def test_json_response_when_gateway_ignores_stream(self) -> None:
        """有些网关忽略 stream: true 直接回 JSON —— 要能认出来，不能当没内容。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"model": "m", "choices": [{"message": {"content": "网关直给"}}]},
                headers={"content-type": "application/json"},
            )

        done = _done(list(_client(handler).stream_with_tools(messages=[], tools=[])))
        assert done.content == "网关直给"
        assert done.degraded is True

    def test_sse_body_without_content_type_is_still_parsed(self) -> None:
        """content-type 缺失时（代理常见），仍要能按 SSE 解析。"""
        body = f"data: {_delta('无类型头')}\n\ndata: [DONE]\n\n"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body.encode("utf-8"))

        done = _done(list(_client(handler).stream_with_tools(messages=[], tools=[])))
        assert done.content == "无类型头"

    def test_no_tools_omits_tool_fields(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _sse([_delta("好"), "[DONE]"])

        list(_client(handler).stream_with_tools(messages=[], tools=[]))
        assert "tools" not in seen["body"]
