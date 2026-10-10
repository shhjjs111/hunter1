"""候选人画像的领域校验测试 —— 纯模型，无 IO。

盯住的是一条**顺序**：字段约束（`max_length`）必须跑在「剥离空白」之后。
顺序反了不会报错、也不会丢数据，只是把**合法输入**判成非法，而且拒绝理由与实际
不符 —— 这类缺陷在 HTTP 面上表现为「用户明明只写了 1 个关键词，却被告知超过 50 条上限」。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hunter1.slices.scoring.models import (
    MAX_PROFILE_ITEM_CHARS,
    MAX_PROFILE_KEYWORDS,
    MAX_PROFILE_SUMMARY_CHARS,
    CandidateProfile,
)


class TestBlankItemsDoNotConsumeTheQuota:
    def test_one_real_keyword_plus_full_quota_of_blanks_is_accepted(self) -> None:
        """空白条目不该占配额：只写 1 个关键词的画像必须被接受。

        修复前（校验在字段约束**之后**）：`["AI"] + [""] * 50` → 51 条 → 422
        「List should have at most 50 items after validation, not 51」——
        用户看着自己唯一那个关键词，不知道该改什么。
        """
        profile = CandidateProfile(keywords=["AI"] + [""] * MAX_PROFILE_KEYWORDS)
        assert profile.keywords == ["AI"]

    def test_whitespace_only_items_are_dropped_too(self) -> None:
        profile = CandidateProfile(keywords=["  ", "\t", "AI"])
        assert profile.keywords == ["AI"]

    def test_actually_too_many_real_items_is_still_rejected(self) -> None:
        """收紧的是「空白不算数」，不是上限本身 —— 真超量照样拒。"""
        with pytest.raises(ValidationError):
            CandidateProfile(keywords=["AI"] * (MAX_PROFILE_KEYWORDS + 1))

    def test_dropping_every_item_still_violates_the_invariant(self) -> None:
        """剔掉空白之后一条不剩 = 画像为空 —— 不变量仍要拦住它。

        （否则 `["", ""]` 会被当成「已配置」，评分照跑，而提示词里写着「（未指定）」。）
        """
        with pytest.raises(ValidationError):
            CandidateProfile(keywords=["", "   "], directions=[""], summary=" ")


class TestWhitespaceDoesNotCountTowardTheSummaryLimit:
    def test_exactly_at_the_limit_plus_a_trailing_newline_is_accepted(self) -> None:
        """2000 字符正文 + 一个尾换行 = 用户写的 2000 字符，不该被拒。

        修复前：`max_length` 按**未剥离**的原文算 → 2001 → 422「String should have
        at most 2000 characters」，而界面上用户看到的摘要正好是 2000 字。
        """
        profile = CandidateProfile(summary="x" * MAX_PROFILE_SUMMARY_CHARS + "\n")
        assert len(profile.summary) == MAX_PROFILE_SUMMARY_CHARS

    def test_leading_and_trailing_space_is_stripped_before_measuring(self) -> None:
        profile = CandidateProfile(summary="  " + "x" * MAX_PROFILE_SUMMARY_CHARS + "  ")
        assert profile.summary == "x" * MAX_PROFILE_SUMMARY_CHARS

    def test_genuinely_over_the_limit_is_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidateProfile(summary="x" * (MAX_PROFILE_SUMMARY_CHARS + 1))


class TestSingleItemLength:
    def test_item_whitespace_is_stripped_before_measuring(self) -> None:
        item = "x" * MAX_PROFILE_ITEM_CHARS
        assert CandidateProfile(keywords=[f"  {item}  "]).keywords == [item]

    def test_genuinely_overlong_item_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidateProfile(keywords=["x" * (MAX_PROFILE_ITEM_CHARS + 1)])
