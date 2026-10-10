"""`domain.llm` 的单元测试 —— 纯模型，无 IO。

这里只钉一件会被反复问到的事：**失败文案是给用户的，不是给维护者的**。
`LLMError.code` 是机器可读的稳定标识（排查靠它），但它不该单独出现在界面上 ——
实测界面上曾经显示的就是 `LLMError: transport_failed` / `llm failed: transport_failed`。
"""

from __future__ import annotations

import pytest

from hunter1.domain.llm import LLMError, describe_llm_error


class TestDescribeLlmError:
    @pytest.mark.parametrize(
        ("code", "hint"),
        [
            ("transport_failed", "连不上模型端点"),
            ("invalid_url", "端点地址不合法"),
            ("http_401", "API Key"),
            ("http_404", "/v1"),
            ("http_429", "限流"),
            ("http_500", "服务端出错"),
            ("response_empty", "空内容"),
            ("structured_response_invalid", "结构化"),
        ],
    )
    def test_known_codes_get_actionable_chinese(self, code: str, hint: str) -> None:
        """每个已知 code 都要翻成一句能照着做的中文，并且**保留原始 code**。"""
        message = describe_llm_error(LLMError(code))
        assert hint in message, message
        assert code in message, "原始 code 必须留着 —— 它是排查时唯一的机器可读抓手"

    def test_5xx_family_shares_one_hint(self) -> None:
        """`http_5` 前缀覆盖整族：500/502/503 不必各写一条。"""
        assert "服务端出错" in describe_llm_error(LLMError("http_503"))

    def test_unknown_code_still_avoids_a_bare_identifier(self) -> None:
        """认不出来也要给一句人话 —— 不能把裸标识符丢回去。"""
        message = describe_llm_error(LLMError("some_new_code"))
        assert "模型调用失败" in message
        assert "some_new_code" in message

    def test_detail_is_kept_after_the_code(self) -> None:
        """厂商给的原始细节（已脱敏）跟着走，不丢。"""
        message = describe_llm_error(LLMError("http_401", "invalid api key"))
        assert "http_401" in message
        assert "invalid api key" in message

    def test_no_class_name_leaks(self) -> None:
        """类名（`LLMError: …`）不该出现在用户可见文案里。"""
        assert "LLMError" not in describe_llm_error(LLMError("transport_failed"))
