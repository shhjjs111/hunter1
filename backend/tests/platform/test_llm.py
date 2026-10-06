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
    """记录每次请求带的 response_format；带了就 400 拒绝（模拟厂商不支持）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body.get("response_format"))
        if body.get("response_format") is not None:
            return httpx.Response(400, json={"error": {"message": "unsupported format"}})
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
