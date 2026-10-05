"""求职助手用例 —— 自研轻量 agent 循环。

流程：

    用户消息 → 组装上下文（系统提示 + 历史 + 工具定义）
             → 调模型
             → 若返回工具调用 → 执行 → 结果回灌 → 再调模型（循环）
             → 拿到最终文本回复

关键设计选择：
- **工具失败不中断对话**：错误作为工具结果回灌，让模型自己纠正。
- **有刹车**：模型若陷入无限工具调用，达到 `max_iterations` 即停并给兜底回复。
- **换厂商不用改代码**：只依赖 `LLMProvider` 端口。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hunter1.application.ports import LLMProvider
from hunter1.application.tools import ToolRegistry
from hunter1.domain.assistant import Message, Role, ToolResult

DEFAULT_SYSTEM_PROMPT = """你是 Hunter1 的求职助手，帮用户管理秋招投递。

你可以调用工具查询用户的岗位库与投递记录。原则：
- 需要事实（岗位、投递状态、数量）时**先调工具查**，不要凭空回答。
- 查到什么说什么；没查到就如实说没查到，不要编造岗位或数字。
- 回答简短、直接、可执行。
- 只做只读查询与建议；涉及写操作（如记录投递）时，明确告诉用户由他确认后再做。"""

DEFAULT_MAX_ITERATIONS = 6

_TRUNCATED_REPLY = "（助手在多次工具调用后仍未给出结论，已停止。请把问题拆小一点再问。）"


@dataclass
class AssistantResult:
    """一次助手回合的结果。"""

    reply: str
    tool_results: list[ToolResult] = field(default_factory=list)
    iterations: int = 0
    truncated: bool = False
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def run_turn(
    *,
    llm: LLMProvider,
    registry: ToolRegistry,
    messages: list[Message],
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> AssistantResult:
    """跑一轮助手对话（可能包含多次模型调用与工具执行）。"""
    if not messages:
        raise ValueError("assistant turn requires at least one message")

    conversation: list[Message] = [Message(role=Role.SYSTEM, content=system_prompt), *messages]
    tools = registry.openai_tools()
    tool_results: list[ToolResult] = []
    model: str | None = None
    input_tokens = 0
    output_tokens = 0

    for iteration in range(1, max_iterations + 1):
        response = llm.complete_with_tools(
            messages=conversation,
            tools=tools,
        )
        model = response.model
        input_tokens += response.input_tokens or 0
        output_tokens += response.output_tokens or 0

        if not response.has_tool_calls:
            return AssistantResult(
                reply=response.content,
                tool_results=tool_results,
                iterations=iteration,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        conversation.append(
            Message(role=Role.ASSISTANT, content=response.content, tool_calls=response.tool_calls)
        )

        for call in response.tool_calls:
            result = registry.invoke(call.name, call.arguments, call_id=call.id)
            tool_results.append(result)
            conversation.append(result.as_message())

        # 模型只调工具、不说结论时，把「请给出结论」再推一把（最后一轮）
        if iteration == max_iterations:
            break

    return AssistantResult(
        reply=_TRUNCATED_REPLY,
        tool_results=tool_results,
        iterations=max_iterations,
        truncated=True,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


__all__ = [
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_SYSTEM_PROMPT",
    "AssistantResult",
    "run_turn",
]
