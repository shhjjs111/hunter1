"""agent 循环单元测试 —— 假 LLM + 假工具，全程离线。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
"""

from __future__ import annotations

import pytest

from hunter1.domain.assistant import Message, Role, ToolCall
from hunter1.domain.llm import LLMError, LLMResponse
from hunter1.slices.assistant.service import AssistantResult, run_turn
from hunter1.slices.assistant.tools import ToolRegistry, tool


def _search(keyword: str = "") -> str:
    """搜索岗位。"""
    return f"找到含「{keyword}」的岗位 2 条"


REGISTRY = ToolRegistry([tool(_search, name="search_jobs")])


class ScriptedLLM:
    """按脚本依次返回响应；记录每次收到的消息。"""

    def close(self) -> None:
        """端口要求：释放底层资源；内存假件是 no-op。"""

    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def _next(self) -> LLMResponse:
        if not self.script:
            raise AssertionError("LLM 被调用次数超出脚本")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def complete(self, **_kw) -> LLMResponse:
        raise AssertionError("助手应使用带工具的调用")

    def complete_structured(self, **_kw) -> LLMResponse:
        raise AssertionError("助手应使用带工具的调用")

    def complete_with_tools(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        return self._next()


def _text(content: str) -> LLMResponse:
    return LLMResponse(content=content, model="fake")


def _tool_call(name: str, args: dict, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        content="",
        model="fake",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
    )


class TestDirectAnswer:
    def test_returns_text_without_tools(self) -> None:
        llm = ScriptedLLM([_text("你好！我可以帮你查岗位。")])
        result = run_turn(llm=llm, registry=REGISTRY, messages=[Message(Role.USER, "你好")])
        assert isinstance(result, AssistantResult)
        assert result.reply == "你好！我可以帮你查岗位。"
        assert result.tool_results == []
        assert result.iterations == 1

    def test_system_prompt_is_injected_once(self) -> None:
        llm = ScriptedLLM([_text("好的")])
        run_turn(
            llm=llm,
            registry=REGISTRY,
            messages=[Message(Role.USER, "在吗")],
            system_prompt="你是求职助手",
        )
        sent = llm.calls[0]["messages"]  # 领域 Message 列表（序列化在基础设施层）
        assert sent[0].role == "system"
        assert sent[0].content == "你是求职助手"

    def test_tools_are_offered_to_model(self) -> None:
        llm = ScriptedLLM([_text("好的")])
        run_turn(llm=llm, registry=REGISTRY, messages=[Message(Role.USER, "在吗")])
        tools = llm.calls[0]["tools"]
        assert tools[0]["function"]["name"] == "search_jobs"


class TestToolCalling:
    def test_tool_call_then_answer(self) -> None:
        llm = ScriptedLLM(
            [
                _tool_call("search_jobs", {"keyword": "产品"}),
                _text("我找到 2 条产品岗。"),
            ]
        )
        result = run_turn(llm=llm, registry=REGISTRY, messages=[Message(Role.USER, "找产品岗")])
        assert result.reply == "我找到 2 条产品岗。"
        assert result.iterations == 2
        assert len(result.tool_results) == 1
        assert result.tool_results[0].ok
        assert "产品" in result.tool_results[0].content

    def test_tool_result_is_fed_back_to_model(self) -> None:
        llm = ScriptedLLM([_tool_call("search_jobs", {"keyword": "产品"}), _text("好")])
        run_turn(llm=llm, registry=REGISTRY, messages=[Message(Role.USER, "找产品岗")])
        second_call_messages = llm.calls[1]["messages"]
        roles = [m.role for m in second_call_messages]
        assert Role.TOOL in roles  # 工具结果确实回灌了
        tool_msg = next(m for m in second_call_messages if m.role is Role.TOOL)
        assert "产品" in tool_msg.content

    def test_multiple_tool_calls_in_one_turn(self) -> None:
        llm = ScriptedLLM(
            [
                LLMResponse(
                    content="",
                    model="fake",
                    tool_calls=[
                        ToolCall(id="c1", name="search_jobs", arguments={"keyword": "产品"}),
                        ToolCall(id="c2", name="search_jobs", arguments={"keyword": "运营"}),
                    ],
                ),
                _text("两条都查了"),
            ]
        )
        result = run_turn(llm=llm, registry=REGISTRY, messages=[Message(Role.USER, "都查")])
        assert len(result.tool_results) == 2
        assert result.reply == "两条都查了"

    def test_unknown_tool_error_is_fed_back(self) -> None:
        llm = ScriptedLLM([_tool_call("ghost_tool", {}), _text("抱歉，我换个方式")])
        result = run_turn(llm=llm, registry=REGISTRY, messages=[Message(Role.USER, "用幽灵工具")])
        assert len(result.tool_results) == 1
        assert not result.tool_results[0].ok
        assert "ghost_tool" in (result.tool_results[0].error or "")
        # 模型拿到错误后仍能给出回复
        assert result.reply == "抱歉，我换个方式"


class TestSafetyLimits:
    def test_stops_after_max_iterations(self) -> None:
        """模型陷入无限工具调用时要能刹车，而不是永远转下去。"""
        llm = ScriptedLLM([_tool_call("search_jobs", {"keyword": "x"}) for _ in range(10)])
        result = run_turn(
            llm=llm, registry=REGISTRY, messages=[Message(Role.USER, "循环")], max_iterations=3
        )
        assert result.iterations == 3
        assert result.truncated is True
        assert result.reply  # 给出一个兜底回复，不是空

    def test_llm_error_propagates(self) -> None:
        llm = ScriptedLLM([LLMError("http_429")])
        with pytest.raises(LLMError):
            run_turn(llm=llm, registry=REGISTRY, messages=[Message(Role.USER, "你好")])

    def test_empty_messages_rejected(self) -> None:
        with pytest.raises(ValueError):
            run_turn(llm=ScriptedLLM([]), registry=REGISTRY, messages=[])


class TestToolChoiceOffered:
    def test_no_tools_registered_still_works(self) -> None:
        llm = ScriptedLLM([_text("我没有工具，但可以聊聊。")])
        result = run_turn(llm=llm, registry=ToolRegistry(), messages=[Message(Role.USER, "你好")])
        assert result.reply == "我没有工具，但可以聊聊。"
