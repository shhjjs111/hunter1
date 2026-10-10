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
import math
import random
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

import httpx

from hunter1.domain.assistant import Message, ToolCall, parse_tool_calls, to_openai_messages
from hunter1.domain.llm import LLMError, LLMResponse, StreamComplete, TextDelta
from hunter1.platform.llm.streaming import parse_sse_lines
from hunter1.platform.text import redact_secret

TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
STRUCTURED_MODES = ("json_schema", "json_object", "none")

#: 重试之间的等待上限（秒）。
#:
#: 厂商给的 `Retry-After` 也可能离谱（真见过 3600）。照单全收会把一个 HTTP 请求挂成
#: 小时级，而端点后面还排着别的请求、用户正等着这次回答 —— 等 20 秒已经比「快速失败
#: 让用户重试」更差，再长就只是把线程占死。
MAX_RETRY_DELAY = 20.0

#: 指数退避的基数（秒）：第 n 次失败后等 `base * 2**(n-1)`，再叠抖动。
RETRY_BASE_DELAY = 0.5


def _parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """解析 `Retry-After` 头；看不懂就返回 None（调用方退回退避）。

    RFC 7231 允许两种形态：**秒数**或 **HTTP 日期**。只认前者会让带日期的厂商白给
    一个头（少，但确实有）；日期已过按 0 处理 —— 立刻重试正是它的字面意思。
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        # `nan` / `inf` 也要当成「看不懂」：`float('nan')` 解析成功，而
        # `max(0.0, nan)` 在 Python 里回落到 0.0 —— 于是「有头就照办」的分支给出
        # **立即重试**，既不退避也不抖动，正好与「看不懂就退回退避」相反。
        # （`inf` 本身会被调用方的上限裁掉，但一起拒掉更一致，也让 NaN 无处漏。）
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return max(0.0, moment.timestamp() - (time.time() if now is None else now))


# 「这个端点拒绝过哪些结构化格式」—— **进程级**记忆，按 (base_url, model) 键控。
#
# 为什么不能放在客户端实例上：客户端是**每请求新建**的（见 `main._runtime_llm`
# ——用户改配置要立刻生效，不能启动时缓存）。实例级记忆活不过一次请求，于是厂商
# 不支持某格式时，每次调用都要重吃一个 400 再降级：白白多打 1-2 个注定失败的
# 付费请求，还多赔一段延迟。
#
# 为什么按 (base_url, model) 键控而不是一个全局集合：拒绝情况是**端点特有**的。
# 用一个全局集合会让 A 厂商不支持的格式连累 B 厂商（它其实支持），把可用能力
# 无谓地降级掉。键里的 base_url 已归一化（strip + 去尾斜杠）。
#
# 线程安全：FastAPI 的同步端点跑在线程池里，可能并发。取用 setdefault 时加锁；
# 之后的 in / add 是单个原子操作，CPython 下无需额外保护。
#
# 上限：键里含**用户可控**的 base_url，而每次改配置都会用新键调一次 `_shared_rejected_modes`。
# 不设上限就是只增不减（长期跑的进程里越攒越多）。这份记忆是**优化**不是正确性 ——
# 丢掉一条只是下次多打一个注定失败的请求（与上面「宁可漏记」同一个取舍），所以
# 按 LRU 淘汰是安全的；反过来误留一条才是永久性的，所以淘汰只看「多久没用」。
MAX_REJECTION_KEYS = 64

_rejected_modes_by_endpoint: OrderedDict[tuple[str, str], set[str]] = OrderedDict()
_rejected_modes_lock = Lock()


def _shared_rejected_modes(base_url: str, model: str) -> set[str]:
    """取该端点的共享拒绝记忆（跨客户端实例存活），并把它标成「最近用过」。"""
    key = (base_url, model)
    with _rejected_modes_lock:
        modes = _rejected_modes_by_endpoint.get(key)
        if modes is not None:
            _rejected_modes_by_endpoint.move_to_end(key)
            return modes
        modes = set()
        _rejected_modes_by_endpoint[key] = modes
        while len(_rejected_modes_by_endpoint) > MAX_REJECTION_KEYS:
            _rejected_modes_by_endpoint.popitem(last=False)
        return modes


def clear_rejected_modes() -> None:
    """清空记忆。给测试隔离用 —— 生产代码没有调用它的理由。"""
    with _rejected_modes_lock:
        _rejected_modes_by_endpoint.clear()


def rejected_mode_endpoints() -> tuple[tuple[str, str], ...]:
    """当前记着的端点键，**旧 → 新**。

    给测试用（断言上限与淘汰顺序）；排查时也用得上 —— 「这个端点为什么不再试结构化
    输出」的答案就在这份记忆里，而它此前没有任何读取口。
    """
    with _rejected_modes_lock:
        return tuple(_rejected_modes_by_endpoint)


# 「这个 400 是在说格式不支持吗」的特征词。只认**明确指向格式参数**的措辞，
# 不做泛化的 "unsupported"/"invalid" 匹配 —— 那会把上下文超长、参数非法之类
# 统统误判成格式问题，进而永久降级该端点。
#
# 取舍方向是**宁可漏记**：漏记时本次照样降级（下一个模式往往能过），只是下次
# 多打一个注定失败的请求；误记则是静默且永久的（该端点再也不试结构化输出）。
_FORMAT_REJECTION_HINTS = (
    "response_format",  # OpenAI / DeepSeek / 多数网关：直接点名参数
    "json_schema",
    "json_object",
    "json mode",
    "structured output",
)


def _looks_like_format_rejection(text: str) -> bool:
    """响应体是否确实在说「不认这个 response_format」。"""
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _FORMAT_REJECTION_HINTS)


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
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        cleaned_url = (base_url or "").strip().rstrip("/")
        if not cleaned_url:
            raise ValueError("base_url is required")
        parsed = urlsplit(cleaned_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"invalid base_url: {base_url!r}")
        # `netloc` 非空不代表端口合法：`https://api.example.com:notaport/v1` 照样
        # 通过上面的检查，却会在 httpx 建请求时抛 `InvalidURL`。在这里提前挡住 ——
        # 构造期失败（配置页能显示原因）胜过请求期失败（用户看到 500）。
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError(f"invalid base_url: {base_url!r}") from exc
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
        self._random = random_fn
        self._client = httpx.Client(timeout=self.timeout, transport=transport)
        # 记住哪些模式被厂商拒绝过，避免每次都重试一遍。
        # **跨实例共享**（按端点键控）—— 客户端每请求新建，实例级记忆等于没有。
        self._rejected_modes = _shared_rejected_modes(self.base_url, self.model)

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
            finish_reason=_finish_reason(data),
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
            finish_reason=_finish_reason(data),
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

        刻意不做重试：流已经吐了一部分再重试会重复输出。为此把降级限制在
        **尚未产出任何事件**之前（连接建立阶段）—— 一旦吐过 TextDelta，中途断连
        （ReadTimeout / RemoteProtocolError 都是 `httpx.HTTPError` 子类）会抛
        `LLMError("stream_interrupted")` 而不是降级重发：否则全文会被当成新的
        增量再吐一遍，消费方（`assistant/service.run_turn_stream`）累加后就是
        「半截 + 全文」的重复输出，并落进对话历史。连接建立阶段的失败仍走非流式
        降级路径，那边自带重试，足以覆盖「代理不支持流式」这类故障。
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

        # 「是否已经吐过事件」—— 这是「能否降级」的分界线。吐过之后断连若降级重发，
        # 全文会被当成新的增量再吐一遍（重复输出并落库）；未吐过时降级则安全。
        yielded = False
        try:
            with self._client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    response.read()  # 先读完，连接才能复用
                    if response.status_code == 400 or response.status_code in TRANSIENT_STATUS:
                        # 400：多半是这家不接受 stream；429/5xx：暂时性故障，
                        # 交给非流式路径重试。两者都走降级 —— 此时尚未 yield，
                        # 重发不会重复。（降级自身的失败会以 LLMError 抛出，
                        # 不是 httpx.HTTPError，因此不会被下面这个 except 吞掉。）
                        yield from self._degraded_stream(
                            messages=messages, tools=tools, max_tokens=max_tokens
                        )
                        return
                    raise LLMError(
                        f"http_{response.status_code}", _short(self._redact(response.text))
                    )

                content_type = response.headers.get("content-type", "")
                if "event-stream" in content_type.lower():
                    # 逐事件 yield 而不是 `yield from`：需要在每个事件后记录「已吐过」，
                    # 好让紧接着的断连走「抛错」而非「降级重发」。
                    for event in parse_sse_lines(response.iter_lines(), default_model=self.model):
                        yielded = True
                        yield event
                    return
                body = response.read()
        except httpx.InvalidURL as exc:
            # 建连前 URL 非法（坏端口、坏 IDNA 主机名…）。`InvalidURL` 的 mro 是
            # `InvalidURL → Exception` —— **不是** `httpx.HTTPError`，只 catch HTTPError
            # 会让它裸穿到调用方（只认 LLMError）→ 用户配置里的畸形 URL 变成 500。
            # 重试无意义：URL 不会自己变好。
            raise LLMError("invalid_url", _short(str(exc))) from exc
        except httpx.HTTPError as exc:
            if yielded:
                # 流已产出内容 —— 重发必然重复。如实抛错，由调用方决定半截结果怎么办
                # （assistant 的流式端点已有 except → SSE error 事件 的路径接住）。
                raise LLMError("stream_interrupted", _short(str(exc))) from exc
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
            # 网关把流式请求当一次性处理：这里的 finish_reason 同样是截断信号
            # （降级网关正是常见的那一类）。
            finish_reason=_finish_reason(data),
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
            # 降级＝这家厂商没能真正流式输出；它往往是把流式请求转成了非流式
            # max_tokens 调用 —— 那里的 finish_reason 是**唯一**的截断信号，
            # 丢掉它，降级路径的截断提示就永久失效。
            finish_reason=response.finish_reason,
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

            # 空 content 要按「本次尝试失败」处理、降级到下一档，而不是直接抛出去
            # 终结整条降级链：`_extract_content` 抛的 `response_empty` 原先会穿透
            # for 循环，而同一类失败（不可解析）却会 continue —— 两条失败路径两种
            # 命运，且被终结的这条本来更可能被下一档救回（模型只是不肯照 schema 说）。
            try:
                content = _extract_content(data)
            except LLMError as exc:
                if exc.code != "response_empty":
                    raise
                last_error = exc
                continue

            try:
                parsed = _parse_json_payload(content)
            except ValueError as exc:
                last_error = LLMError("structured_response_invalid", str(exc))
                continue

            return LLMResponse(
                content=json.dumps(parsed, ensure_ascii=False),
                finish_reason=_finish_reason(data),
                model=str(data.get("model") or self.model),
                input_tokens=_usage(data, "prompt_tokens"),
                output_tokens=_usage(data, "completion_tokens"),
                structured_mode=mode,
            )

        if last_error is not None:
            raise last_error
        raise LLMError("structured_response_invalid")

    # ---- 传输 ----

    def _redact(self, text: str) -> str:
        """把厂商回显在错误体里的 API Key 抹掉（见 `platform.text.redact_secret`）。"""
        return redact_secret(text, self.api_key)

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        """这次失败之后等多久再试。三件事，按优先级：

        1. **听 `Retry-After`**。厂商明确说了「X 秒后再来」，自己按节奏重试只会继续
           吃 429（还可能被判成滥用）。有头就照办 —— 只做上限裁剪，不加抖动：
           那个数字是它给的承诺，不是我们的估计。
        2. **指数退避**（没给头时）。上游过载时线性增长等于持续加压（0.5/1.0/1.5s
           对「正在恢复」的服务几乎等于不停敲）；指数才有实质让路。
        3. **抖动**。多个客户端/线程同时失败会齐步重试，形成周期性脉冲把刚恢复的
           服务再打下去。用「等抖动」（一半固定 + 一半随机）而不是全抖动：前者保证
           至少等到标称值的一半，不会退化成立刻重试。
        """
        if response is not None:
            hinted = _parse_retry_after(response.headers.get("Retry-After"))
            if hinted is not None:
                return min(hinted, MAX_RETRY_DELAY)
        ceiling = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
        half = ceiling / 2
        return half + half * self._random()

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
            except httpx.InvalidURL as exc:
                # `InvalidURL` 的 mro 是 `InvalidURL → Exception` —— **不是**
                # `httpx.HTTPError`。只 catch HTTPError 会让它裸穿（调用方只认
                # `LLMError`）：配置页存下的畸形 URL 于是变成 500 + traceback。
                # 重试没有意义 —— URL 不会自己变好。
                raise LLMError("invalid_url", _short(str(exc))) from exc
            except httpx.HTTPError:
                code = "transport_failed"
                if attempt >= self.max_retries:
                    raise LLMError(code) from None
                self._sleep(self._retry_delay(attempt, None))
                continue

            if response.status_code in TRANSIENT_STATUS:
                code = f"http_{response.status_code}"
                if attempt >= self.max_retries:
                    raise LLMError(code)
                self._sleep(self._retry_delay(attempt, response))
                continue

            if response.status_code >= 400:
                # 400 且带了 response_format：厂商可能是「不接受这种格式」，也可能只是
                # 这次请求有别的问题（上下文超长、参数非法…）。
                #
                # 两种情况都值得**本次**降级 —— 下一个模式或许能过（json_schema 太复杂
                # 时 json_object 常常可以）。但只有前者该被**永久记住**：误记的代价是
                # 该端点此后再也不试结构化输出（静默降级成纯提示词，日志里毫无痕迹），
                # 而漏记的代价只是下次多打一个注定失败的请求。
                if (
                    response.status_code == 400
                    and reject_mode is not None
                    and reject_mode != "none"
                ):
                    if _looks_like_format_rejection(response.text):
                        self._rejected_modes.add(reject_mode)
                    raise LLMError("format_unsupported", _short(self._redact(response.text)))
                raise LLMError(f"http_{response.status_code}", _short(self._redact(response.text)))

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


def _finish_reason(data: dict[str, Any]) -> str | None:
    """取**非流式**响应体里的结束原因（`length` = 被 token 上限截断）。

    与 `streaming.parse_sse_lines` 同一语义：取第一个非空的原因。有的网关不返回
    这一项 —— 那就如实返回 `None`（「不知道」），不要猜成 `stop`：把「不知道」说成
    「正常说完」正是截断被静默吞掉的那条路。
    """
    choices = data.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return None


def _short(text: str, limit: int = 200) -> str:
    return (text or "").strip()[:limit]


__all__ = [
    "PROVIDER_PRESETS",
    "LLMError",
    "LLMResponse",
    "OpenAICompatibleClient",
    "ProviderPreset",
    "clear_rejected_modes",
    "resolve_preset",
]
