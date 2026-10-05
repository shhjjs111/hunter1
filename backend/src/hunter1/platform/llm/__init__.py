"""统一 LLM provider 层 —— 任意 OpenAI 兼容端点，**不硬绑任何厂商**。

与旧系统的关键差异：旧系统 `packages/model_policy.py` 的 `official_base()` 只允许
`api.deepseek.com`，`official_model()` 只允许两个模型；非官方端点直接抛
`ValueError("Only the official DeepSeek API is supported")`。结果是评分链与求职助手
全被锁死在一家。hunter1 反过来：**默认走 OpenAI 兼容协议，端点由用户填**。

不假设厂商都支持高级特性 —— `complete_structured` 带**分级降级链**：
    json_schema → json_object → 纯提示词（并从 markdown 围栏里抠 JSON）
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from hunter1.domain.assistant import Message, ToolCall, parse_tool_calls, to_openai_messages
from hunter1.domain.llm import LLMError, LLMResponse, StreamComplete, TextDelta
from hunter1.platform.llm.streaming import parse_sse_lines

TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
STRUCTURED_MODES = ("json_schema", "json_object", "none")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class ProviderPreset:
    """常见厂商预设。用户也可完全手填 base_url，预设只是省事。"""

    name: str
    base_url: str
    default_model: str


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset("deepseek", "https://api.deepseek.com/v1", "deepseek-chat"),
    "qwen": ProviderPreset(
        "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"
    ),
    "zhipu": ProviderPreset("zhipu", "https://open.bigmodel.cn/api/paas/v4", "glm-4-plus"),
    "kimi": ProviderPreset("kimi", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "siliconflow": ProviderPreset(
        "siliconflow", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct"
    ),
    "openai": ProviderPreset("openai", "https://api.openai.com/v1", "gpt-4o-mini"),
}


class OpenAICompatibleClient:
    """OpenAI 兼容 `/chat/completions` 客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 45.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        cleaned_url = (base_url or "").strip().rstrip("/")
        if not cleaned_url:
            raise ValueError("base_url is required")
        parsed = urlsplit(cleaned_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"invalid base_url: {base_url!r}")
        if not (api_key or "").strip():
            raise ValueError("api_key is required")
        if not (model or "").strip():
            raise ValueError("model is required")

        self.base_url = cleaned_url
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = max(1.0, float(timeout))
        self.max_retries = max(1, int(max_retries))
        self._sleep = sleep
        self._client = httpx.Client(timeout=self.timeout, transport=transport)
        # 记住哪些模式被厂商拒绝过，避免每次都重试一遍
        self._rejected_modes: set[str] = set()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAICompatibleClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- 纯文本 ----

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        data = self._post(payload)
        return LLMResponse(
            content=_extract_content(data),
            model=str(data.get("model") or self.model),
            tool_calls=_extract_tool_calls(data),
            input_tokens=_usage(data, "prompt_tokens"),
            output_tokens=_usage(data, "completion_tokens"),
        )

    # ---- 带工具（function calling）----

    def complete_with_tools(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """带工具定义的调用。工具调用与文本回复都可能出现在响应里。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        data = self._post(payload)
        return LLMResponse(
            content=_extract_content_lenient(data),
            model=str(data.get("model") or self.model),
            tool_calls=_extract_tool_calls(data),
            input_tokens=_usage(data, "prompt_tokens"),
            output_tokens=_usage(data, "completion_tokens"),
        )

    # ---- 带工具的流式（不支持时自动降级）----

    def stream_with_tools(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> Iterator[TextDelta | StreamComplete]:
        """带工具的流式调用：先吐增量文本，最后吐一个完整结果。

        契约是**这个方法永远可用**：厂商不支持 streaming 时（400）、网关把
        streaming 剥掉了、或连接建不起来，它会退化为一次非流式调用，把结果
        作为「一个 TextDelta + StreamComplete(degraded=True)」交出来。
        调用方因此不必自己判断「这家厂商支不支持流式」。

        与 `complete_with_tools` 的差别只在**怎么拿到结果**，不在**拿到什么**：
        降级路径同样保留工具调用 —— 否则助手会悄悄退化成「只会聊天」。

        刻意不做重试：流已经吐了一部分再重试会重复输出。连接建立阶段的失败
        改走非流式降级路径，那边自带重试，足以覆盖「代理不支持流式」这类故障。
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        try:
            with self._client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    response.read()  # 先读完，连接才能复用
                    if response.status_code == 400 or response.status_code in TRANSIENT_STATUS:
                        # 400：多半是这家不接受 stream；429/5xx：暂时性故障，
                        # 交给非流式路径重试。两者都走降级。
                        yield from self._degraded_stream(
                            messages=messages, tools=tools, max_tokens=max_tokens
                        )
                        return
                    raise LLMError(f"http_{response.status_code}", _short(response.text))

                content_type = response.headers.get("content-type", "")
                if "event-stream" in content_type.lower():
                    yield from parse_sse_lines(response.iter_lines(), default_model=self.model)
                    return
                body = response.read()
        except httpx.HTTPError:
            yield from self._degraded_stream(messages=messages, tools=tools, max_tokens=max_tokens)
            return

        yield from self._stream_from_body(body)

    def _stream_from_body(self, body: bytes) -> Iterator[TextDelta | StreamComplete]:
        """响应体不是 SSE 分支时的处理。

        两种真实情况：网关忽略了 `stream: true` 直接回 JSON；或代理没带
        `content-type` 但正文其实是 SSE。先按 JSON 试，失败再按 SSE 试。
        """
        try:
            data = json.loads(body)
        except ValueError:
            yield from parse_sse_lines(
                body.decode("utf-8", "replace").splitlines(), default_model=self.model
            )
            return

        if not isinstance(data, dict):
            raise LLMError("response_invalid")
        content = _extract_content_lenient(data)
        if content:
            yield TextDelta(content)
        yield StreamComplete(
            content=content,
            model=str(data.get("model") or self.model),
            tool_calls=_extract_tool_calls(data),
            input_tokens=_usage(data, "prompt_tokens"),
            output_tokens=_usage(data, "completion_tokens"),
            degraded=True,
        )

    def _degraded_stream(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
        max_tokens: int | None,
    ) -> Iterator[TextDelta | StreamComplete]:
        """非流式兜底：把一次性结果包成同形状的事件流。"""
        response = self.complete_with_tools(messages=messages, tools=tools, max_tokens=max_tokens)
        if response.content:
            yield TextDelta(response.content)
        yield StreamComplete(
            content=response.content,
            model=response.model,
            tool_calls=response.tool_calls,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            degraded=True,
        )

    # ---- 结构化输出（带降级链）----

    def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        name: str = "hunter1_result",
    ) -> LLMResponse:
        last_error: LLMError | None = None

        for mode in STRUCTURED_MODES:
            if mode in self._rejected_modes:
                continue
            instruction = (
                user_prompt if mode != "none" else _prompt_only_instruction(user_prompt, schema)
            )
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction},
                ],
                "stream": False,
            }
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if mode != "none":
                payload["response_format"] = _response_format(mode, schema, name)

            try:
                data = self._post(payload, reject_mode=mode)
            except LLMError as exc:
                # 厂商拒绝这种 response_format → 记住并降级
                if exc.code == "format_unsupported":
                    continue
                last_error = exc
                raise

            content = _extract_content(data)
            try:
                parsed = _parse_json_payload(content)
            except ValueError as exc:
                last_error = LLMError("structured_response_invalid", str(exc))
                continue

            return LLMResponse(
                content=json.dumps(parsed, ensure_ascii=False),
                model=str(data.get("model") or self.model),
                input_tokens=_usage(data, "prompt_tokens"),
                output_tokens=_usage(data, "completion_tokens"),
                structured_mode=mode,
            )

        if last_error is not None:
            raise last_error
        raise LLMError("structured_response_invalid")

    # ---- 传输 ----

    def _post(self, payload: dict[str, Any], *, reject_mode: str | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        code = "transport_failed"

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.post(url, headers=headers, json=payload)
            except httpx.HTTPError:
                code = "transport_failed"
                if attempt >= self.max_retries:
                    raise LLMError(code) from None
                self._sleep(0.5 * attempt)
                continue

            if response.status_code in TRANSIENT_STATUS:
                code = f"http_{response.status_code}"
                if attempt >= self.max_retries:
                    raise LLMError(code)
                self._sleep(0.5 * attempt)
                continue

            if response.status_code >= 400:
                # 400 且带了 response_format 时，很可能是厂商不支持该格式 → 触发降级
                if (
                    response.status_code == 400
                    and reject_mode is not None
                    and reject_mode != "none"
                ):
                    self._rejected_modes.add(reject_mode)
                    raise LLMError("format_unsupported", _short(response.text))
                raise LLMError(f"http_{response.status_code}", _short(response.text))

            try:
                data = response.json()
            except ValueError:
                raise LLMError("response_invalid") from None
            if not isinstance(data, dict):
                raise LLMError("response_invalid")
            return data

        raise LLMError(code)


def resolve_preset(
    name: str,
    *,
    api_key: str,
    model: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 45.0,
    max_retries: int = 2,
) -> OpenAICompatibleClient:
    """用内置预设构造客户端。`name` 不存在则 KeyError。"""
    preset = PROVIDER_PRESETS[name]
    return OpenAICompatibleClient(
        base_url=preset.base_url,
        api_key=api_key,
        model=model or preset.default_model,
        transport=transport,
        timeout=timeout,
        max_retries=max_retries,
    )


# ---- 内部工具 ----


def _response_format(mode: str, schema: dict[str, Any], name: str) -> dict[str, Any]:
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": schema, "strict": False},
        }
    return {"type": "json_object"}


def _prompt_only_instruction(user_prompt: str, schema: dict[str, Any]) -> str:
    return (
        f"{user_prompt}\n\n"
        "只输出一个 JSON 对象，不要任何解释或 markdown 围栏。"
        f"它必须符合此 JSON Schema：\n{json.dumps(schema, ensure_ascii=False)}"
    )


def _extract_content(data: dict[str, Any]) -> str:
    """纯文本调用：内容为空视为失败。"""
    content = _raw_message_content(data)
    if not content.strip():
        raise LLMError("response_empty")
    return content


def _extract_content_lenient(data: dict[str, Any]) -> str:
    """带工具的调用：内容可以为空（模型只调工具、不说话）。"""
    return _raw_message_content(data)


def _raw_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("response_invalid")
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMError("response_invalid")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMError("response_invalid")
    content = message.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        raise LLMError("response_invalid")
    return content


def _extract_tool_calls(data: dict[str, Any]) -> list[ToolCall]:
    """从响应里解析工具调用；没有则返回空列表。"""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    first = choices[0]
    if not isinstance(first, dict):
        return []
    message = first.get("message")
    if not isinstance(message, dict):
        return []
    raw = message.get("tool_calls")
    if not isinstance(raw, list):
        return []
    return parse_tool_calls(raw)


def _parse_json_payload(content: str) -> Any:
    """从模型输出里抠出 JSON —— 容忍 markdown 围栏与前后废话。"""
    text = content.strip()
    fence = _JSON_FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    # 退一步：抓第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            pass
    raise ValueError("no parseable JSON in model output")


def _usage(data: dict[str, Any], key: str) -> int | None:
    usage = data.get("usage")
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if isinstance(value, int) and value >= 0 else None


def _short(text: str, limit: int = 200) -> str:
    return (text or "").strip()[:limit]


__all__ = [
    "PROVIDER_PRESETS",
    "LLMError",
    "LLMResponse",
    "OpenAICompatibleClient",
    "ProviderPreset",
    "resolve_preset",
]
