"""评分的领域类型与规则 —— 纯模型，无 IO 依赖。

（从旧 `domain/matching.py` 迁入：本切片自己的模型不再住在共享 domain 里，
「一个切片 = 一个领地」由此在类型层面也成立。）
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hunter1.platform.text import normalize_job_title


class ScoringError(RuntimeError):
    """评分过程失败（模型输出不可用 / 传输错误）。"""


class CandidateProfile(BaseModel):
    """候选人画像。至少要有一项信号，否则评分无从谈起。"""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list)
    directions: list[str] = Field(default_factory=list)
    summary: str = ""

    @field_validator("summary")
    @classmethod
    def _strip_summary(cls, value: str) -> str:
        return (value or "").strip()

    def model_post_init(self, _context: object) -> None:
        if not self.keywords and not self.directions and not self.summary:
            raise ValueError("candidate profile needs at least one of keywords/directions/summary")

    @property
    def has_content(self) -> bool:
        return bool(self.keywords or self.directions or self.summary)

    def matches_title(self, title: str) -> bool:
        """标题是否命中任一关键词（归一化后子串匹配）。"""
        normalized = normalize_job_title(title)
        if not normalized:
            return False
        return any(
            normalize_job_title(keyword) in normalized
            for keyword in self.keywords
            if normalize_job_title(keyword)
        )


class ScoreCard(BaseModel):
    """一次评分的结论。`score` 是唯一必填项 —— 模型给不出分就不算评过。"""

    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=100)
    advantages: str | None = None
    gaps: str | None = None
    summary: str | None = None
    model: str | None = None
    prompt_version: str | None = None


__all__ = ["CandidateProfile", "ScoreCard", "ScoringError"]
