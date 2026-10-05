"""评分用例 —— 编排「岗位 + 画像 → 模型 → 结论」。

只依赖 `LLMProvider` 端口与领域类型，不接触具体厂商 —— 因此可用假 LLM
完全离线测试，也意味着**换任何厂商都不需要改这段代码**。
"""

from __future__ import annotations

import json

from hunter1.application.ports import LLMProvider
from hunter1.domain.llm import LLMError
from hunter1.domain.matching import (
    PROMPT_VERSION,
    SCORE_SCHEMA,
    SYSTEM_PROMPT,
    CandidateProfile,
    ScoreCard,
    ScoringError,
    build_user_prompt,
)
from hunter1.domain.models import Job


def score_job(
    *,
    job: Job,
    profile: CandidateProfile,
    llm: LLMProvider,
    max_tokens: int | None = 1200,
) -> ScoreCard:
    """给一个岗位打分。

    失败一律抛 `ScoringError`（含原因），不返回 0 分 —— 「没评上」和
    「评了 0 分」是两件事，混在一起会让岗位库的信噪比崩掉。
    """
    title = (job.title or "").strip()
    if not title:
        raise ScoringError("job title is empty; cannot score")

    user_prompt = build_user_prompt(
        title=title,
        company=job.company_id,
        jd_text=job.jd_raw,
        profile=profile,
    )

    try:
        response = llm.complete_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=SCORE_SCHEMA,
            max_tokens=max_tokens,
        )
    except LLMError as exc:
        raise ScoringError(f"llm failed: {exc}") from exc

    try:
        payload = json.loads(response.content)
    except ValueError as exc:
        raise ScoringError(f"model returned non-JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ScoringError("model returned non-object JSON")

    raw_score = payload.get("score")
    if isinstance(raw_score, bool) or not isinstance(raw_score, int):
        raise ScoringError(f"score missing or not an integer: {raw_score!r}")
    if not 0 <= raw_score <= 100:
        raise ScoringError(f"score out of range: {raw_score}")

    return ScoreCard(
        score=raw_score,
        advantages=_text(payload.get("advantages")),
        gaps=_text(payload.get("gaps")),
        summary=_text(payload.get("summary")),
        model=response.model,
        prompt_version=PROMPT_VERSION,
    )


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = ["score_job"]
