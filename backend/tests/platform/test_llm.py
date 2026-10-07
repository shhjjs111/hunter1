"""统一 LLM provider 层单元测试 —— 离线（httpx.MockTransport）。

这是「所有 API 都能用」的落地关口。与旧系统的关键差异：
旧系统 `model_policy.py` 用白名单硬绑 DeepSeek（非官方端点直接抛错），
hunter1 必须**接受任意 OpenAI 兼容端点**。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

import json
from typing import ClassVar

import httpx
import pytest

from hunter1.platform.llm import (
    PROVIDER_PRESETS,
    LLMError,
    OpenAICompatibleClient,
    clear_rejected_modes,
    resolve_preset,
)


def _client(handler, **kwargs) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url=kwargs.pop("base_url", "https://api.example.com/v1"),
        api_key=kwargs.pop("api_key", "sk-test"),
        model=kwargs.pop("model", "test-model"),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _ok_response(content: str = "你好", model: str = "test-model") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        },
    )


class TestRequestShape:
    def test_posts_to_chat_completions(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization", "")
            seen["body"] = json.loads(request.content)
            return _ok_response()

        _client(handler).complete(system_prompt="sys", user_prompt="usr")

        assert seen["url"] == "https://api.example.com/v1/chat/completions"
        assert seen["auth"] == "Bearer sk-test"
        body = seen["body"]
        assert body["model"] == "test-model"
        assert body["messages"][0] == {"role": "system", "content": "sys"}
        assert body["messages"][1] == {"role": "user", "content": "usr"}

    def test_trailing_slash_in_base_url_is_handled(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _ok_response()

        _client(handler, base_url="https://api.example.com/v1/").complete(
            system_prompt="s", user_prompt="u"
        )
        assert seen["url"] == "https://api.example.com/v1/chat/completions"

    def test_max_tokens_is_forwarded(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _ok_response()

        _client(handler).complete(system_prompt="s", user_prompt="u", max_tokens=1234)
        assert seen["body"]["max_tokens"] == 1234  # type: ignore[index]


class TestArbitraryProviders:
    """核心差异点：任意厂商端点都必须接受，不做白名单。"""

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.deepseek.com/v1",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://open.bigmodel.cn/api/paas/v4",
            "https://api.moonshot.cn/v1",
            "https://api.siliconflow.cn/v1",
            "https://api.openai.com/v1",
            "https://my-own-proxy.internal/llm/v1",
            "http://127.0.0.1:8787/v1",
        ],
    )
    def test_accepts_any_openai_compatible_base_url(self, base_url: str) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok_response()

        client = _client(handler, base_url=base_url)
        assert client.complete(system_prompt="s", user_prompt="u").content == "你好"

    def test_rejects_blank_api_key(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleClient(base_url="https://a.com/v1", api_key="  ", model="m")

    def test_rejects_blank_model(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleClient(base_url="https://a.com/v1", api_key="k", model="")

    def test_rejects_base_url_without_scheme(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatibleClient(base_url="api.example.com/v1", api_key="k", model="m")


class TestResponseParsing:
    def test_returns_content_and_usage(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok_response("结果文本")

        result = _client(handler).complete(system_prompt="s", user_prompt="u")
        assert result.content == "结果文本"
        assert result.model == "test-model"
        assert result.input_tokens == 11
        assert result.output_tokens == 7

    def test_empty_content_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok_response("   ")

        with pytest.raises(LLMError) as excinfo:
            _client(handler).complete(system_prompt="s", user_prompt="u")
        assert excinfo.value.code == "response_empty"

    def test_malformed_body_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": True})

        with pytest.raises(LLMError) as excinfo:
            _client(handler).complete(system_prompt="s", user_prompt="u")
        assert excinfo.value.code == "response_invalid"

    def test_missing_usage_is_tolerated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"model": "m", "choices": [{"message": {"content": "ok"}}]}
            )

        result = _client(handler).complete(system_prompt="s", user_prompt="u")
        assert result.input_tokens is None
        assert result.output_tokens is None


class TestErrorsAndRetry:
    def test_401_is_not_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401, json={"error": "bad key"})

        with pytest.raises(LLMError) as excinfo:
            _client(handler).complete(system_prompt="s", user_prompt="u")
        assert excinfo.value.code == "http_401"
        assert calls["n"] == 1

    def test_429_is_retried_then_raises(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429)

        with pytest.raises(LLMError) as excinfo:
            _client(handler, max_retries=2).complete(system_prompt="s", user_prompt="u")
        assert excinfo.value.code == "http_429"
        assert calls["n"] == 2

    def test_transport_error_is_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectError("no route")

        with pytest.raises(LLMError) as excinfo:
            _client(handler, max_retries=2).complete(system_prompt="s", user_prompt="u")
        assert excinfo.value.code == "transport_failed"
        assert calls["n"] == 2

    def test_recovers_on_retry(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503) if calls["n"] < 2 else _ok_response("成功")

        result = _client(handler, max_retries=2).complete(system_prompt="s", user_prompt="u")
        assert result.content == "成功"


class TestStructuredOutput:
    SCHEMA: ClassVar[dict[str, object]] = {
        "type": "object",
        "properties": {"score": {"type": "integer"}},
        "required": ["score"],
    }

    def test_json_schema_mode_sends_response_format(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return _ok_response('{"score": 88}')

        result = _client(handler).complete_structured(
            system_prompt="s", user_prompt="u", schema=self.SCHEMA
        )
        body = seen["body"]
        assert body["response_format"]["type"] == "json_schema"  # type: ignore[index]
        assert json.loads(result.content) == {"score": 88}

    def test_falls_back_when_json_schema_unsupported(self) -> None:
        """厂商不支持 json_schema 时应自动降级，而不是直接失败。"""
        modes: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            fmt = body.get("response_format", {}).get("type", "none")
            modes.append(fmt)
            if fmt == "json_schema":
                return httpx.Response(400, json={"error": "response_format unsupported"})
            return _ok_response('{"score": 66}')

        result = _client(handler).complete_structured(
            system_prompt="s", user_prompt="u", schema=self.SCHEMA
        )
        assert modes[0] == "json_schema"
        assert modes[1] == "json_object"
        assert json.loads(result.content) == {"score": 66}

    def test_falls_back_to_prompt_only_when_all_formats_rejected(self) -> None:
        modes: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            fmt = body.get("response_format", {}).get("type", "none")
            modes.append(fmt)
            if fmt != "none":
                return httpx.Response(400, json={"error": "unsupported"})
            return _ok_response('```json\n{"score": 42}\n```')

        result = _client(handler).complete_structured(
            system_prompt="s", user_prompt="u", schema=self.SCHEMA
        )
        assert modes == ["json_schema", "json_object", "none"]
        assert json.loads(result.content) == {"score": 42}  # 剥掉 markdown 围栏

    def test_unparseable_structured_output_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok_response("这不是 JSON")

        with pytest.raises(LLMError) as excinfo:
            _client(handler).complete_structured(
                system_prompt="s", user_prompt="u", schema=self.SCHEMA
            )
        assert excinfo.value.code == "structured_response_invalid"


class TestPresets:
    def test_presets_cover_common_vendors(self) -> None:
        for key in ["deepseek", "qwen", "zhipu", "kimi", "siliconflow", "openai"]:
            assert key in PROVIDER_PRESETS

    def test_resolve_preset_builds_client(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok_response()

        client = resolve_preset("deepseek", api_key="sk-x", transport=httpx.MockTransport(handler))
        assert "deepseek" in client.base_url
        assert client.model  # 有默认模型

    def test_resolve_preset_allows_model_override(self) -> None:
        client = resolve_preset("openai", api_key="sk-x", model="gpt-4o-mini")
        assert client.model == "gpt-4o-mini"

    def test_unknown_preset_raises(self) -> None:
        with pytest.raises(KeyError):
            resolve_preset("not-a-vendor", api_key="sk-x")


SCHEMA = {"type": "object", "properties": {"score": {"type": "integer"}}, "required": ["score"]}


def _rejecting_handler(seen: list[object]) -> object:
    """记录每次请求带的 response_format；带了就 400 拒绝（模拟厂商不支持）。

    响应体按**真实形状**写：厂商的 400 一定会点名出问题的参数（`response_format`），
    而不是一句笼统的 "unsupported format"。这一点在本用例里有实际后果 ——
    「是否永久记住该模式被拒」的判据就看响应体措辞（见
    `_looks_like_format_rejection`）。早先用笼统措辞的版本会让生产逻辑
    判不出来，假数据不自知地偏离了真实。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body.get("response_format"))
        if body.get("response_format") is not None:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "Invalid parameter: 'response_format' of type 'json_schema' "
                            "is not supported with this model."
                        ),
                        "type": "invalid_request_error",
                        "param": "response_format",
                    }
                },
            )
        return _ok_response('{"score": 7}')

    return handler


@pytest.fixture(autouse=True)
def _isolate_rejected_modes():
    """每个用例前清空「端点拒绝记忆」。

    那份记忆是**进程级**的（故意的：客户端每请求新建，记忆必须跨实例存活）。
    代价是模块级状态会跨用例泄漏 —— 不清的话，前面用例发现的「json_schema 被拒」
    会让后面用例直接从降级后的模式开始试，断言随之错乱。用 autouse 统一隔离，
    比逐个用例记得清更可靠。
    """
    clear_rejected_modes()
    yield
    clear_rejected_modes()


class TestRejectedModeMemory:
    """「这个端点拒绝过某格式」必须**跨客户端实例**存活。

    客户端是每请求新建的（`main._runtime_llm`：用户改配置要立刻生效，不能启动时
    缓存）。若记忆挂在实例上，它活不过一次请求 —— 厂商不支持某格式时每次调用都
    要重吃一个 400 再降级，白白多打注定失败的付费请求。
    """

    def test_first_call_discovers_then_degrades(self) -> None:
        """首次调用：依次试到能用的模式为止。"""
        seen: list[object] = []
        client = _client(_rejecting_handler(seen))
        client.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        client.close()
        # json_schema → 400，json_object → 400，none → 成功
        assert [f is not None for f in seen] == [True, True, False], f"实际 {seen}"

    def test_next_client_skips_already_rejected_modes(self) -> None:
        """**核心断言**：换一个客户端实例（= 下一个请求）不该重试已被拒的模式。"""
        seen: list[object] = []
        handler = _rejecting_handler(seen)

        first = _client(handler)
        first.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        first.close()

        seen.clear()
        second = _client(handler)  # 新实例，模拟下一个请求
        second.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        second.close()

        assert seen == [None], f"应直接用 none 模式，一个多余请求都不该发；实际 {seen}"

    def test_memory_is_scoped_to_endpoint(self) -> None:
        """拒绝记忆按 (base_url, model) 隔离 —— 不能让一家的问题连累另一家。

        某厂商不支持 json_schema，不代表另一家不支持；若共用一个全局集合，
        后者会被无谓降级掉（能力白丢）。
        """
        seen_a: list[object] = []
        client_a = _client(_rejecting_handler(seen_a), base_url="https://a.example.com/v1")
        client_a.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        client_a.close()

        seen_b: list[object] = []
        client_b = _client(_rejecting_handler(seen_b), base_url="https://b.example.com/v1")
        client_b.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        client_b.close()

        # B 端点应从 json_schema 开始试（它没被拒过）
        assert seen_b[0] is not None, f"B 端点不该被 A 的拒绝记录连累；实际 {seen_b}"


# 真实厂商拒绝 `response_format` 时的措辞样本（取自各家文档/实测报错的形状）。
REAL_FORMAT_REJECTIONS = [
    # OpenAI：json_schema 不被该模型支持
    "Invalid parameter: 'response_format' of type 'json_schema' is not supported with this model.",
    # 网关/自建：只认 json_object
    "unsupported value: response_format",
    # 中文网关
    "json_schema is not supported",
]


def _plain_400_handler(seen: list[object], message: str) -> object:
    """400 拒绝，但错误文本与「格式不支持」**无关**（如上下文超长）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body.get("response_format"))
        if body.get("response_format") is not None:
            return httpx.Response(400, json={"error": {"message": message}})
        return _ok_response('{"score": 7}')

    return handler


class TestFormatRejectionHeuristic:
    """「永久记住这个端点拒绝某格式」只在**确实**是格式问题时才该发生。

    误记的代价是静默且永久的：该端点此后再也不试结构化输出（连试都不试），
    直接降级成纯提示词 —— 用户永远拿不到格式保障，而日志里没有任何异常。
    """

    def test_non_format_400_is_not_remembered_across_clients(self) -> None:
        """上下文超长导致的 400 不该被当成「格式被拒」记下来。"""
        seen: list[object] = []
        handler = _plain_400_handler(seen, "This model's maximum context length is 8192 tokens.")

        first = _client(handler)
        first.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        first.close()

        seen.clear()
        second = _client(handler)  # 下一个请求（新实例）
        second.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        second.close()

        assert seen[0] is not None, f"不该被永久记住 —— 下次仍应从 json_schema 试起；实际 {seen}"

    def test_non_format_400_still_degrades_this_request(self) -> None:
        """本次仍要降级：下一个模式或许能过（只是不永久记住）。

        若这里改成「原样抛 http_400」，厂商措辞不在特征词表里时用户会**彻底失败** ——
        比多打两个请求糟得多。
        """
        seen: list[object] = []
        handler = _plain_400_handler(seen, "maximum context length exceeded")
        client = _client(handler)
        client.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        client.close()

        assert [f is not None for f in seen] == [True, True, False], f"实际 {seen}"

    @pytest.mark.parametrize("message", REAL_FORMAT_REJECTIONS)
    def test_real_vendor_wording_is_remembered(self, message: str) -> None:
        """措辞确实指向格式 → 记住，下一个请求不再浪费一次 400。"""
        seen: list[object] = []
        handler = _plain_400_handler(seen, message)

        first = _client(handler)
        first.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        first.close()

        seen.clear()
        second = _client(handler)
        second.complete_structured(system_prompt="s", user_prompt="u", schema=SCHEMA)
        second.close()

        assert seen == [None], f"应直接用 none 模式，不再发多余请求；实际 {seen}"
