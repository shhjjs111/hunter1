"""大模型交互的领域契约 —— 纯数据与错误词汇，无 IO 依赖。

放在 domain 层的原因：应用层需要「模型返回什么形状」「失败怎么表达」这两件事，
但**不该**因此依赖任何具体厂商实现。基础设施实现这些契约
（见 `hunter1.platform.llm`），并在失败时抛出这里定义的 `LLMError`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hunter1.domain.assistant import ToolCall


class LLMError(RuntimeError):
    """一次 LLM 调用最终失败。`code` 稳定可判（如 http_401 / response_empty）。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}{': ' + message if message else ''}")
        self.code = code


#: 常见失败码 → 一句**可行动的中文**（前缀匹配，`http_5` 覆盖 5xx 一族）。
#:
#: 为什么要翻：界面此前把 `LLMError` 原样显示成 `LLMError: transport_failed` /
#: `llm failed: transport_failed` —— 那是给维护者的标识符，用户从中得不到任何下一步
#: 动作；而本项目在别处一贯给可执行的指引（模型未配置 → 「请先在「配置」页填好
#: base_url / 模型 / API Key」）。原始 code 仍然保留在括号里 —— 它是排查时唯一的机器
#: 可读抓手，只是不该**单独**出现在用户眼前。
_LLM_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    ("transport_failed", "连不上模型端点：检查 Base URL、网络与代理是否可达"),
    ("invalid_url", "端点地址不合法：检查协议、主机名与端口"),
    ("http_401", "端点拒绝了这把 API Key（401）：检查 Key 是否正确、是否过期"),
    ("http_403", "端点拒绝访问（403）：检查该 Key 的权限"),
    ("http_404", "端点路径不存在（404）：base_url 多半少了一段（如 /v1）"),
    ("http_429", "被端点限流（429）：稍等片刻再试"),
    ("http_5", "端点服务端出错（5xx）：多半是对方故障，稍后重试"),
    ("response_empty", "模型返回了空内容：换一个模型或重试"),
    ("response_invalid", "端点返回的不是 OpenAI 兼容格式：确认 base_url 指向兼容端点"),
    ("structured_response_invalid", "模型没能给出符合要求的结构化结果：重试或换一个模型"),
    ("format_unsupported", "该端点不支持结构化输出"),
    ("stream_interrupted", "流式传输中途断开：回答可能不完整，请重试"),
    ("stream_error", "端点在流里报了错"),
)


def describe_llm_error(exc: LLMError) -> str:
    """把 `LLMError` 翻成「可行动的中文（原始 code）」。

    认不出 code 时给一句通用说明 —— 也**不**把原始英文丢给用户，但 code 照旧带在
    括号里（排查要靠它）。
    """
    code = exc.code or ""
    detail = str(exc)[len(code) :].lstrip(": ").strip()
    hint = next((text for prefix, text in _LLM_ERROR_HINTS if code.startswith(prefix)), None)
    origin = f"{code}{'：' + detail if detail else ''}"
    return f"{hint or '模型调用失败'}（{origin}）"


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
    #: 厂商给的结束原因（`stop` / `length` / `tool_calls` / …），没给则 `None`。
    #: 与 `StreamComplete.finish_reason` 同义：`length` 表示回答被 token 上限**截断**，
    #: 是半截的。一次性的 `/assistant/turn` 与流式的 `/assistant/stream` 是同一资源的
    #: 两个面（见 router 的注释），截断必须两边都认得出 —— 只认流式的话，非流式那条路
    #: 会把半截回答当完整答案落库，而两者在外观上无从分辨。
    finish_reason: str | None = None

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
    #: 厂商给的结束原因（`stop` / `length` / `tool_calls` / …），没给则 `None`。
    #: 存在的意义是区分「正常说完」与「被 token 上限截断」—— `length` 时回答是
    #: **半截的**，而它在外观上与完整回答无异（这正是要显式化的原因）。
    #: 不把「没收到 [DONE]」当失败：大量兼容网关就这么收尾，属既有容忍设计。
    finish_reason: str | None = None

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
    "describe_llm_error",
]
