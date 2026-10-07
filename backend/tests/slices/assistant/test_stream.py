"""流式 agent 循环测试 —— 假流式 LLM + 假工具，全程离线。

除了「能流」本身，这里盯两件事：
- **事件顺序**：前端按序渲染，顺序错了就会看到「工具结果出现在问题之前」；
- **与非流式路径行为一致**：`run_turn` 与 `run_turn_stream` 是两套循环，
  必须有测试锁住它们不漂移（同一脚本 → 同样的回复与工具结果）。
"""

from __future__ import annotations

from typing import Any

import pytest

from hunter1.domain.assistant import Message, Role, ToolCall
from hunter1.domain.llm import LLMError, LLMResponse, StreamComplete, TextDelta
from hunter1.slices.assistant.service import (
    ToolFinished,
    ToolStarted,
    TurnDone,
    run_turn,
    run_turn_stream,
)
from hunter1.slices.assistant.tools import ToolRegistry, tool


def _search(keyword: str = "") -> str:
    """搜索岗位。"""
    return f"找到含「{keyword}」的岗位 2 条"


def _boom() -> str:
    """一个会炸的工具。"""
    raise RuntimeError("工具炸了")


REGISTRY = ToolRegistry([tool(_search, name="search_jobs"), tool(_boom, name="boom")])


class ScriptedLLM:
    """按脚本依次返回响应的假 LLM（同步路径）。

    原先是从 `tests/application/test_assistant_loop.py` 跨目录借来的 —— 那让本文件
    隐式依赖另一个测试目录（对方一删这边就红）。就地定义，自包含。
    """

    def close(self) -> None:
        """端口要求：释放底层资源；内存假件是 no-op。"""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def _next(self) -> LLMResponse:
        if not self.script:
            raise AssertionError("LLM 被调用次数超出脚本")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def complete(self, **_kw: Any) -> LLMResponse:
        raise AssertionError("助手应使用带工具的调用")

    def complete_structured(self, **_kw: Any) -> LLMResponse:
        raise AssertionError("助手应使用带工具的调用")

    def complete_with_tools(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return self._next()


class StreamingLLM:
    """按脚本产出事件流的假 LLM。

    脚本每一项是 `list[TextDelta | StreamComplete]`（一次 `stream_with_tools`
    调用产出的事件），或一个异常（调用时抛出）。
    """

    def close(self) -> None:
        """端口要求：释放底层资源；内存假件是 no-op。"""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def stream_with_tools(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("LLM 被调用次数超出脚本")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        yield from item

    def complete(self, **_kw: Any) -> LLMResponse:
        raise AssertionError("流式路径不该调用非流式方法")

    def complete_structured(self, **_kw: Any) -> LLMResponse:
        raise AssertionError("流式路径不该调用非流式方法")

    def complete_with_tools(self, **_kw: Any) -> LLMResponse:
        raise AssertionError("流式路径不该调用非流式方法")


def _stream_text(*parts: str, degraded: bool = False, model: str = "fake") -> list[Any]:
    return [
        *[TextDelta(part) for part in parts],
        StreamComplete(content="".join(parts), model=model, degraded=degraded),
    ]


def _stream_tool(name: str, args: dict[str, Any], call_id: str = "c1") -> list[Any]:
    return [
        StreamComplete(
            content="",
            model="fake",
            tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        )
    ]


def _run(llm: StreamingLLM, **kwargs: Any) -> list[Any]:
    return list(run_turn_stream(llm=llm, registry=REGISTRY, **kwargs))


class TestTextStreaming:
    def test_deltas_are_yielded_as_they_arrive(self) -> None:
        llm = StreamingLLM([_stream_text("你", "好", "！")])
        events = _run(llm, messages=[Message(Role.USER, "你好")])
        texts = [e.text for e in events if isinstance(e, TextDelta)]
        assert texts == ["你", "好", "！"]

    def test_turn_done_is_last_and_carries_full_reply(self) -> None:
        llm = StreamingLLM([_stream_text("你", "好")])
        events = _run(llm, messages=[Message(Role.USER, "你好")])
        assert isinstance(events[-1], TurnDone)
        assert events[-1].reply == "你好"
        assert events[-1].truncated is False
        assert events[-1].degraded is False

    def test_no_tool_events_for_direct_answer(self) -> None:
        llm = StreamingLLM([_stream_text("直接回答")])
        events = _run(llm, messages=[Message(Role.USER, "在吗")])
        assert not [e for e in events if isinstance(e, (ToolStarted, ToolFinished))]

    def test_system_prompt_injected_once(self) -> None:
        llm = StreamingLLM([_stream_text("好")])
        _run(llm, messages=[Message(Role.USER, "在吗")], system_prompt="你是助手")
        sent = llm.calls[0]["messages"]
        assert sent[0].role == Role.SYSTEM
        assert sent[0].content == "你是助手"

    def test_tools_are_offered(self) -> None:
        llm = StreamingLLM([_stream_text("好")])
        _run(llm, messages=[Message(Role.USER, "在吗")])
        assert llm.calls[0]["tools"][0]["function"]["name"] in {"search_jobs", "boom"}


class TestToolStreaming:
    def test_tool_events_are_emitted_around_execution(self) -> None:
        llm = StreamingLLM(
            [
                _stream_tool("search_jobs", {"keyword": "产品"}),
                _stream_text("查到 2 条产品岗。"),
            ]
        )
        events = _run(llm, messages=[Message(Role.USER, "找产品岗")])
        kinds = [type(e).__name__ for e in events]
        assert kinds == ["ToolStarted", "ToolFinished", "TextDelta", "TurnDone"]

    def test_tool_started_carries_name_and_arguments(self) -> None:
        llm = StreamingLLM([_stream_tool("search_jobs", {"keyword": "产品"}), _stream_text("好了")])
        events = _run(llm, messages=[Message(Role.USER, "找产品岗")])
        started = next(e for e in events if isinstance(e, ToolStarted))
        assert started.name == "search_jobs"
        assert started.arguments == {"keyword": "产品"}

    def test_tool_finished_carries_result(self) -> None:
        llm = StreamingLLM([_stream_tool("search_jobs", {"keyword": "产品"}), _stream_text("好了")])
        events = _run(llm, messages=[Message(Role.USER, "找产品岗")])
        finished = next(e for e in events if isinstance(e, ToolFinished))
        assert finished.ok is True
        assert "产品" in finished.content
        assert finished.error is None

    def test_failing_tool_is_reported_but_conversation_continues(self) -> None:
        llm = StreamingLLM([_stream_tool("boom", {}), _stream_text("抱歉，我换个方式")])
        events = _run(llm, messages=[Message(Role.USER, "用会炸的工具")])
        finished = next(e for e in events if isinstance(e, ToolFinished))
        assert finished.ok is False
        assert "工具炸了" in (finished.error or "")
        assert events[-1].reply == "抱歉，我换个方式"

    def test_unknown_tool_is_reported(self) -> None:
        llm = StreamingLLM([_stream_tool("ghost", {}), _stream_text("换个方式")])
        events = _run(llm, messages=[Message(Role.USER, "幽灵工具")])
        finished = next(e for e in events if isinstance(e, ToolFinished))
        assert finished.ok is False
        assert "ghost" in (finished.error or "")

    def test_tool_result_is_fed_back_to_model(self) -> None:
        llm = StreamingLLM([_stream_tool("search_jobs", {"keyword": "产品"}), _stream_text("好了")])
        _run(llm, messages=[Message(Role.USER, "找产品岗")])
        second = llm.calls[1]["messages"]
        roles = [m.role for m in second]
        assert Role.TOOL in roles
        tool_message = next(m for m in second if m.role is Role.TOOL)
        assert "产品" in tool_message.content

    def test_multiple_tool_calls_in_one_round(self) -> None:
        llm = StreamingLLM(
            [
                [
                    StreamComplete(
                        content="",
                        model="fake",
                        tool_calls=[
                            ToolCall(id="c1", name="search_jobs", arguments={"keyword": "产品"}),
                            ToolCall(id="c2", name="search_jobs", arguments={"keyword": "运营"}),
                        ],
                    )
                ],
                _stream_text("两条都查了"),
            ]
        )
        events = _run(llm, messages=[Message(Role.USER, "都查")])
        assert len([e for e in events if isinstance(e, ToolStarted)]) == 2
        assert len([e for e in events if isinstance(e, ToolFinished)]) == 2

    def test_turn_done_collects_tool_results(self) -> None:
        llm = StreamingLLM([_stream_tool("search_jobs", {"keyword": "产品"}), _stream_text("好了")])
        done = _run(llm, messages=[Message(Role.USER, "找产品岗")])[-1]
        assert len(done.tool_results) == 1
        assert done.tool_results[0].ok


class TestSafetyAndDegradation:
    def test_truncates_after_max_iterations(self) -> None:
        llm = StreamingLLM([_stream_tool("search_jobs", {}, f"c{i}") for i in range(10)])
        events = _run(llm, messages=[Message(Role.USER, "循环")], max_iterations=3)
        done = events[-1]
        assert done.truncated is True
        assert done.reply  # 兜底回复，不是空

    def test_degraded_flag_propagates_to_turn_done(self) -> None:
        """厂商不支持流式时，回合结果要如实标记 —— 界面据此提示用户。"""
        llm = StreamingLLM([_stream_text("整段回答", degraded=True)])
        done = _run(llm, messages=[Message(Role.USER, "你好")])[-1]
        assert done.degraded is True

    def test_llm_error_propagates(self) -> None:
        llm = StreamingLLM([LLMError("http_401")])
        with pytest.raises(LLMError):
            _run(llm, messages=[Message(Role.USER, "你好")])

    def test_empty_messages_rejected(self) -> None:
        with pytest.raises(ValueError):
            _run(StreamingLLM([]), messages=[])

    def test_token_usage_is_accumulated(self) -> None:
        llm = StreamingLLM(
            [
                [
                    TextDelta("a"),
                    StreamComplete(content="a", model="m", input_tokens=10, output_tokens=4),
                ],
            ]
        )
        done = _run(llm, messages=[Message(Role.USER, "x")])[-1]
        assert done.input_tokens == 10
        assert done.output_tokens == 4

    def test_no_tools_registered_still_streams(self) -> None:
        llm = StreamingLLM([_stream_text("我没有工具")])
        events = list(
            run_turn_stream(llm=llm, registry=ToolRegistry(), messages=[Message(Role.USER, "你好")])
        )
        assert events[-1].reply == "我没有工具"


class TestParityWithNonStreaming:
    """两条路径（同步 / 流式）不许漂移。"""

    def _scripted(self) -> list[Any]:
        return [
            LLMResponse(
                content="",
                model="fake",
                tool_calls=[ToolCall(id="c1", name="search_jobs", arguments={"keyword": "产品"})],
            ),
            LLMResponse(content="查到 2 条产品岗。", model="fake"),
        ]

    def test_same_script_yields_same_reply_and_tools(self) -> None:

        messages = [Message(Role.USER, "找产品岗")]
        sync = run_turn(llm=ScriptedLLM(self._scripted()), registry=REGISTRY, messages=messages)  # type: ignore[arg-type]

        stream_script = [
            _stream_tool("search_jobs", {"keyword": "产品"}),
            _stream_text("查到 2 条产品岗。"),
        ]
        streamed = list(
            run_turn_stream(
                llm=StreamingLLM(stream_script),  # type: ignore[arg-type]
                registry=REGISTRY,
                messages=messages,
            )
        )[-1]

        assert streamed.reply == sync.reply
        assert [r.ok for r in streamed.tool_results] == [r.ok for r in sync.tool_results]
        assert [r.content for r in streamed.tool_results] == [r.content for r in sync.tool_results]

    def test_truncation_message_matches(self) -> None:

        messages = [Message(Role.USER, "循环")]
        sync = run_turn(
            llm=ScriptedLLM(
                [
                    LLMResponse(
                        content="",
                        model="f",
                        tool_calls=[ToolCall(id="c", name="search_jobs", arguments={})],
                    )
                    for _ in range(5)
                ]
            ),
            registry=REGISTRY,
            messages=messages,
            max_iterations=2,
        )  # type: ignore[arg-type]

        streamed = list(
            run_turn_stream(
                llm=StreamingLLM([_stream_tool("search_jobs", {}) for _ in range(5)]),  # type: ignore[arg-type]
                registry=REGISTRY,
                messages=messages,
                max_iterations=2,
            )
        )[-1]
        assert streamed.reply == sync.reply
        assert streamed.truncated == sync.truncated
