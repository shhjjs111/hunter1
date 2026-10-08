"""平台文本工具（`platform.text`）单元测试 —— 岗位标题归一化。

TDD 纪律：本文件先于实现编写（当时为 RED；`hunter1.platform.text` 已落地，此后应保持全绿）。
归一化结果用于「同题折叠」比较，因此必须稳定、可复现。
"""

from __future__ import annotations

import pytest

from hunter1.platform.text import normalize_job_title


class TestNormalizeJobTitle:
    """归一化后应可直接用于同题判定。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # 去首尾空白 + 去内部空白 + 小写
            ("  AI 产品经理  ", "ai产品经理"),
            # 中文括号补充说明整体去除
            ("AI产品经理（2027校招）", "ai产品经理"),
            # 英文括号补充说明整体去除
            ("AI产品经理(急招)", "ai产品经理"),
            # 全角字母归一为半角
            ("ＡＩ产品经理", "ai产品经理"),
            # 已有规范形态不变
            ("ai产品经理", "ai产品经理"),
        ],
    )
    def test_normalizes(self, raw: str, expected: str) -> None:
        assert normalize_job_title(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "（）", "()", "\n\t"])
    def test_blank_like_inputs_collapse_to_empty(self, raw: str) -> None:
        assert normalize_job_title(raw) == ""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("AI产品经理（2027校招（提前批））", "ai产品经理"),
            ("AI产品经理(2027校招(提前批))", "ai产品经理"),
            ("A(B(C(D)))", "a"),
            # 嵌套括号在中间：两种写法的 key 必须一致（否则同题折叠失效）
            ("产品经理（校招（提前批））北京", "产品经理北京"),
        ],
    )
    def test_strips_nested_parentheses(self, raw: str, expected: str) -> None:
        """嵌套括号必须整对剥离、不留残括号（M3：修复前会残留右括号）。"""
        assert normalize_job_title(raw) == expected

    def test_is_idempotent(self) -> None:
        """归一化必须幂等——反复应用结果不变（保证 key 稳定）。"""
        once = normalize_job_title("AI产品经理（2027校招）")
        assert normalize_job_title(once) == once
