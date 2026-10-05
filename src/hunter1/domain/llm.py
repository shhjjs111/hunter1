"""大模型交互的领域契约 —— 纯数据与错误词汇，无 IO 依赖。

放在 domain 层的原因：应用层需要「模型返回什么形状」「失败怎么表达」这两件事，
但**不该**因此依赖任何具体厂商实现。基础设施层实现这些契约（见
`hunter1.infrastructure.llm`），并在失败时抛出这里定义的 `LLMError`。
"""

from __future__ import annotations

from dataclasses import dataclass


class LLMError(RuntimeError):
    """一次 LLM 调用最终失败。`code` 稳定可判（如 http_401 / response_empty）。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}{': ' + message if message else ''}")
        self.code = code


@dataclass
class LLMResponse:
    """一次模型调用的结果。"""

    content: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    structured_mode: str | None = None


__all__ = ["LLMError", "LLMResponse"]
