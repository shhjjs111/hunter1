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

from hunter1.infrastructure.llm import (
    PROVIDER_PRESETS,
    LLMError,
    OpenAICompatibleClient,
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
