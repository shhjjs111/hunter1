"""domain.settings 单元测试 —— 纯模型，无 IO。

配置模型负责的是「什么样的配置才算完整、可用」，而不是去哪儿读写它。
把校验放在模型里，界面层就不必各自重复判断（也不必各自漏判）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hunter1.domain.settings import LLMSettings, plaintext_warning


def _settings(**overrides: object) -> LLMSettings:
    base: dict[str, object] = {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "sk-abcdefghijklmnop",
    }
    base.update(overrides)
    return LLMSettings(**base)  # type: ignore[arg-type]


class TestBaseUrlPort:
    """坏端口要在**输入时**就被拒。

    `https://api.example.com:notaport/v1` 的 netloc 非空，能通过 scheme/netloc
    检查并**存进库**，却会在发请求时抛 `httpx.InvalidURL` —— 到那一步配置页只剩
    500 和一条 traceback。

    实测：把 `_valid_base_url` 里那句 `_ = parts.port` 改成 `_ = 0`，105 条相关
    用例全绿 —— 因为 LLM 客户端自带一份端口校验，而用例只盖住了**那一份**；
    配置页读的是模型这一份。
    """

    def test_bad_port_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _settings(base_url="https://api.example.com:notaport/v1")
        assert "端口" in str(excinfo.value)

    def test_valid_port_is_accepted(self) -> None:
        """配对反例：为了「拒绝坏端口」不能把合法端口一起拒掉。"""
        url = "https://api.example.com:8080/v1"
        assert _settings(base_url=url).base_url == url


class TestPlaintextWarning:
    """明文 http:// 指向公网时要提示（本地/内网不提示）。

    此前整条链路对这件事一个字都不提：Key 与对话内容明文过网，而界面看起来
    和 https 一模一样。提示而不是拒绝 —— 本机 / 内网自建端点用 http 是合理的。
    """

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://api.example.com/v1",
            "http://8.8.8.8/v1",
            "http://llm.example.cn:8000/v1",
        ],
    )
    def test_public_plaintext_is_flagged(self, base_url: str) -> None:
        warning = plaintext_warning(base_url)
        assert warning is not None
        assert "http://" in warning

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.example.com/v1",  # 本来就加密
            "http://localhost:11434/v1",  # 本机 ollama
            "http://127.0.0.1:8000/v1",
            "http://192.168.1.10:8000/v1",  # 内网
            "http://10.0.0.5/v1",
            "http://172.16.3.4:8080/v1",
            "http://ollama:11434/v1",  # 内网单标签名
            "http://llm.local/v1",
            "http://[::1]:8000/v1",
        ],
    )
    def test_local_and_private_are_not_flagged(self, base_url: str) -> None:
        assert plaintext_warning(base_url) is None

    def test_empty_url_is_not_flagged(self) -> None:
        assert plaintext_warning("") is None


class TestLLMSettings:
    def test_minimal_valid_settings(self) -> None:
        settings = _settings()
        assert settings.base_url == "https://api.deepseek.com/v1"
        assert settings.model == "deepseek-chat"

    def test_trailing_slash_is_trimmed(self) -> None:
        assert _settings(base_url="https://api.x.com/v1/").base_url == "https://api.x.com/v1"

    @pytest.mark.parametrize(
        "bad_url",
        ["", "   ", "api.deepseek.com/v1", "ftp://api.x.com", "just-text"],
    )
    def test_invalid_base_url_is_rejected(self, bad_url: str) -> None:
        with pytest.raises(ValidationError):
            _settings(base_url=bad_url)

    def test_empty_model_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(model="   ")

    def test_api_key_may_be_empty_while_configuring(self) -> None:
        """还没填 key 时也应能保存其它配置 —— 是否完整由 is_configured 回答。"""
        settings = _settings(api_key="")
        assert settings.api_key == ""
        assert settings.is_configured is False

    def test_is_configured_true_when_all_present(self) -> None:
        assert _settings().is_configured is True

    def test_temperature_and_max_tokens_are_not_accepted(self) -> None:
        """两个字段已从模型撤除（存而不用）—— 再传就是未定义字段。

        保留这条断言，是为了让「哪天有人想把它们加回来」时必须显式改模型，
        而不是随手传一个被静默忽略的 kwargs。
        """
        with pytest.raises(ValidationError):
            _settings(temperature=0.7)
        with pytest.raises(ValidationError):
            _settings(max_tokens=1200)

    def test_masked_key_never_reveals_the_whole_key(self) -> None:
        """界面要展示「填了哪个 key」，但不能把整个 key 端出去。"""
        masked = _settings(api_key="sk-1234567890abcdef").masked_key()
        assert "1234567890abcdef" not in masked
        assert masked.endswith("cdef")
        assert masked.startswith("sk-")

    def test_masked_key_when_empty(self) -> None:
        assert _settings(api_key="").masked_key() == "（未设置）"

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(secret="x")
