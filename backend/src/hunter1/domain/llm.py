"""大模型交互的领域契约 —— 纯数据与错误词汇，无 IO 依赖。

放在 domain 层的原因：应用层需要「模型返回什么形状」「失败怎么表达」这两件事，
但**不该**因此依赖任何具体厂商实现。基础设施层实现这些契约（见
`hunter1.infrastructure.llm`），并在失败时抛出这里定义的 `LLMError`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hunter1.domain.assistant import ToolCall


class LLMError(RuntimeError):
    """一次 LLM 调用最终失败。`code` 稳定可判（如 http_401 / response_empty）。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}{': ' + message if message else ''}")
        self.code = code


@dataclass
class LLMResponse:
    """一次模型调用的结果。

    `content` 与 `tool_calls` 可以同时存在（有些模型会先说话再调工具），
    也可能只有其一（纯回答 / 纯工具调用）。
    """

    content: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    structured_mode: str | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class TextDelta:
    """模型输出的一段增量文本。"""

    text: str


@dataclass
class StreamComplete:
    """流结束时给出的完整结果。

    `degraded=True` 表示**这家厂商没能真正流式输出**（不支持、网关剥离了
    streaming、或连接建不起来），内容是一次性拿到的。规划 §6.1 要求
    「降级要如实告知用户」，所以这是一个对外可观察的事实，而不是内部细节 ——
    界面据此决定要不要提示「本次回答不是逐字出现的」。
    """

    content: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    degraded: bool = False

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


# 一次流式调用会按序产出：若干 TextDelta，最后一个 StreamComplete。
LLMStreamEvent = TextDelta | StreamComplete


__all__ = [
    "LLMError",
    "LLMResponse",
    "LLMStreamEvent",
    "StreamComplete",
    "TextDelta",
]
