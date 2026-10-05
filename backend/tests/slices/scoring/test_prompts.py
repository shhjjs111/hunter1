"""提示词测试 —— 锁住口径的行为契约。

提示词是**被评测过的资产**：改它要有意识（见 `prompts.py` 的规程）。
这些测试不锁「每个字」，只锁三类会让评分静默失准的结构性行为：
公司名不能是哈希、无 JD 时要降置信度、画像字段必须渲染进去。
"""

from __future__ import annotations

import re

from hunter1.slices.scoring.models import CandidateProfile
from hunter1.slices.scoring.prompts import (
    PROMPT_VERSION,
    SCORE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)

PROFILE = CandidateProfile(keywords=["后端", "Python"], directions=["基础设施"], summary="三年经验")


class TestPromptVersion:
    def test_version_is_present_and_versioned(self) -> None:
        """版本号必须存在且带形态（scoring-vN）—— 它是「这条分是哪版打的」的唯一线索。"""
        assert re.fullmatch(r"scoring-v\d+", PROMPT_VERSION)

    def test_system_prompt_defines_a_scale(self) -> None:
        """口径必须说清「多少分算什么」—— 否则模型各打各的分。"""
        assert "0-100" in SYSTEM_PROMPT
        assert "90-100" in SYSTEM_PROMPT
        assert "0-29" in SYSTEM_PROMPT


class TestScoreSchema:
    def test_score_is_required_integer_in_range(self) -> None:
        properties = SCORE_SCHEMA["properties"]  # type: ignore[index]
        assert properties["score"] == {"type": "integer", "minimum": 0, "maximum": 100}  # type: ignore[index]
        assert SCORE_SCHEMA["required"] == ["score"]

    def test_extra_fields_are_rejected(self) -> None:
        """additionalProperties=False：模型多吐字段时由厂商侧拦住。"""
        assert SCORE_SCHEMA["additionalProperties"] is False


class TestBuildUserPrompt:
    def test_renders_all_profile_sections(self) -> None:
        prompt = build_user_prompt(
            title="后端工程师", company="某公司", jd_text="写服务", profile=PROFILE
        )
        assert "后端、Python" in prompt
        assert "基础设施" in prompt
        assert "三年经验" in prompt

    def test_renders_company_and_title(self) -> None:
        prompt = build_user_prompt(
            title="后端工程师", company="某公司", jd_text="写服务", profile=PROFILE
        )
        assert "- 公司：某公司" in prompt
        assert "- 岗位：后端工程师" in prompt

    def test_missing_jd_asks_for_lower_confidence(self) -> None:
        """没有 JD 时必须显式提示降置信度 —— 否则模型会凭标题给出虚高的分。"""
        prompt = build_user_prompt(title="后端工程师", company="C", jd_text=None, profile=PROFILE)
        assert "未抓到岗位描述" in prompt
        assert "降低置信度" in prompt

    def test_blank_jd_treated_as_missing(self) -> None:
        prompt = build_user_prompt(title="T", company="C", jd_text="   ", profile=PROFILE)
        assert "未抓到岗位描述" in prompt

    def test_empty_profile_lists_render_placeholders(self) -> None:
        """画像可选字段为空时要显式写「未指定」，不能渲染出空行让人误读。"""
        sparse = CandidateProfile(summary="仅摘要")
        prompt = build_user_prompt(title="T", company="C", jd_text="J", profile=sparse)
        assert "（未指定）" in prompt
        assert "仅摘要" in prompt
