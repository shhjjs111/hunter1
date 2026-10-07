"""评分用例单元测试 —— 假 LLM，全程离线。

（从旧 `tests/application/test_score.py` 迁移：断言语义逐条保留，
只改 import 路径。）
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from hunter1.domain.models import Job
from hunter1.platform.llm import LLMError, LLMResponse
from hunter1.slices.scoring.models import CandidateProfile, ScoreCard
from hunter1.slices.scoring.service import score_job

PROFILE = CandidateProfile(
    keywords=["AI产品经理", "大模型产品经理"],
    directions=["AI应用", "B端SaaS"],
    summary="计算机专业本科，独立做过两款 AI 产品。",
)


def _job(**kw: Any) -> Job:
    base: dict[str, Any] = {
        "id": "j1",
        "company_id": "c1",
        "title": "AI产品经理",
        "detail_url": "https://a.com/1",
        "source": "test",
        "jd_raw": "负责大模型应用的规划与落地。",
    }
    base.update(kw)
    return Job(**base)


class FakeLLM:
    """记录调用，返回预置结构化结果。"""

    def close(self) -> None:
        """端口要求：释放底层资源；内存假件是 no-op。"""

    def __init__(
        self, payload: dict[str, Any] | str | None = None, *, boom: Exception | None = None
    ) -> None:
        self.payload = payload if payload is not None else {"score": 82}
        self.boom = boom
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> LLMResponse:
        raise AssertionError("score_job 应使用 complete_structured")

    def complete_structured(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        if self.boom is not None:
            raise self.boom
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return LLMResponse(content=content, model="fake", structured_mode="json_schema")


class TestHappyPath:
    def test_returns_score_card(self) -> None:
        llm = FakeLLM({"score": 82, "advantages": "方向匹配", "gaps": "缺实习", "summary": "可投"})
        card = score_job(job=_job(), profile=PROFILE, llm=llm)
        assert isinstance(card, ScoreCard)
        assert card.score == 82
        assert card.advantages == "方向匹配"
        assert card.gaps == "缺实习"
        assert card.summary == "可投"

    def test_prompt_contains_job_and_profile(self) -> None:
        llm = FakeLLM({"score": 70})
        score_job(job=_job(), profile=PROFILE, llm=llm)
        user_prompt = llm.calls[0]["user_prompt"]
        assert "AI产品经理" in user_prompt
        assert "大模型应用的规划与落地" in user_prompt
        assert "计算机专业本科" in user_prompt

    def test_schema_requires_score(self) -> None:
        llm = FakeLLM({"score": 70})
        score_job(job=_job(), profile=PROFILE, llm=llm)
        schema = llm.calls[0]["schema"]
        assert "score" in schema["properties"]
        assert "score" in schema["required"]


class TestCompanyField:
    """评分提示词里的公司必须是**人读得懂的名字**。

    `company_id` 是身份哈希（domain/models.py 注释写得很清楚），把它喂给
    模型等于送一个无意义字符串 —— 评分质量直接受损。
    """

    def test_prompt_uses_company_name_not_hash(self) -> None:
        llm = FakeLLM({"score": 70})
        score_job(
            job=_job(company_id="6a9e2f112b9cbce6e591b229add2e2b1", company_name="阿里巴巴"),
            profile=PROFILE,
            llm=llm,
        )
        prompt = llm.calls[0]["user_prompt"]
        assert "阿里巴巴" in prompt
        assert "6a9e2f112b9cbce6e591b229add2e2b1" not in prompt

    def test_prompt_falls_back_to_source_without_company_name(self) -> None:
        """没有公司名时退回来源名（与 applications.new_application 同一约定）。"""
        llm = FakeLLM({"score": 70})
        score_job(job=_job(source="offerbiu"), profile=PROFILE, llm=llm)
        assert "offerbiu" in llm.calls[0]["user_prompt"]


class TestPartialPayloads:
    def test_score_only_payload_is_accepted(self) -> None:
        card = score_job(job=_job(), profile=PROFILE, llm=FakeLLM({"score": 55}))
        assert card.score == 55
        assert card.advantages is None
        assert card.gaps is None

    def test_missing_score_raises(self) -> None:
        from hunter1.slices.scoring.models import ScoringError

        with pytest.raises(ScoringError):
            score_job(job=_job(), profile=PROFILE, llm=FakeLLM({"advantages": "只有优点"}))

    def test_out_of_range_score_raises(self) -> None:
        from hunter1.slices.scoring.models import ScoringError

        with pytest.raises(ScoringError):
            score_job(job=_job(), profile=PROFILE, llm=FakeLLM({"score": 150}))

    def test_non_numeric_score_raises(self) -> None:
        from hunter1.slices.scoring.models import ScoringError

        with pytest.raises(ScoringError):
            score_job(job=_job(), profile=PROFILE, llm=FakeLLM({"score": "很高"}))


class TestLLMFailures:
    def test_llm_error_is_wrapped(self) -> None:
        from hunter1.slices.scoring.models import ScoringError

        with pytest.raises(ScoringError) as excinfo:
            score_job(job=_job(), profile=PROFILE, llm=FakeLLM(boom=LLMError("http_429")))
        assert "http_429" in str(excinfo.value)

    def test_no_jd_text_still_scores(self) -> None:
        """没有 JD 正文时也能评（只用标题），但要在提示里说明。"""
        llm = FakeLLM({"score": 60})
        card = score_job(job=_job(jd_raw=None), profile=PROFILE, llm=llm)
        assert card.score == 60
        assert "岗位描述" in llm.calls[0]["user_prompt"] or "JD" in llm.calls[0]["user_prompt"]

    def test_empty_title_raises(self) -> None:
        from hunter1.slices.scoring.models import ScoringError

        with pytest.raises(ScoringError):
            score_job(job=_job(title="   "), profile=PROFILE, llm=FakeLLM({"score": 10}))


class TestProfile:
    def test_profile_requires_some_signal(self) -> None:
        with pytest.raises(ValueError):
            CandidateProfile(keywords=[], directions=[], summary="")

    def test_screen_by_title_keywords(self) -> None:
        assert PROFILE.matches_title("AI产品经理") is True
        assert PROFILE.matches_title("行政专员") is False
