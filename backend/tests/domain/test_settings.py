"""domain.settings 单元测试 —— 纯模型，无 IO。

配置模型负责的是「什么样的配置才算完整、可用」，而不是去哪儿读写它。
把校验放在模型里，界面层就不必各自重复判断（也不必各自漏判）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hunter1.domain.settings import LLMSettings


def _settings(**overrides: object) -> LLMSettings:
    base: dict[str, object] = {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "sk-abcdefghijklmnop",
    }
    base.update(overrides)
    return LLMSettings(**base)  # type: ignore[arg-type]


class TestLLMSettings:
    def test_minimal_valid_settings(self) -> None:
        settings = _settings()
        assert settings.base_url == "https://api.deepseek.com/v1"
        assert settings.temperature is None

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

    def test_temperature_range_is_enforced(self) -> None:
        assert _settings(temperature=0.0).temperature == 0.0
        assert _settings(temperature=2.0).temperature == 2.0
        with pytest.raises(ValidationError):
            _settings(temperature=3.0)

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
