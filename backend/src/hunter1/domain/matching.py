"""匹配评分的领域类型与纯规则 —— 无 IO 依赖。"""

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


# 评分提示词版本：改了提示词就升版本，便于回溯「这条分是哪版打出来的」
PROMPT_VERSION = "scoring-v1"

SYSTEM_PROMPT = """你是资深的求职匹配分析师。根据候选人画像与岗位信息，判断匹配度并给出结论。

评分口径（0-100）：
- 90-100：方向、技能、背景高度契合，强烈建议投递
- 70-89：主要要求满足，值得投递
- 50-69：部分契合，可作为备选
- 30-49：契合度低，除非特别原因否则不建议
- 0-29：明显不匹配

要求：
- 只依据给定信息判断，不要臆测岗位没有写的要求
- 理由要具体，指出依据（哪一条画像 / 哪一条岗位要求）
- 严格输出 JSON，不要加解释或 markdown 围栏"""

SCORE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "advantages": {"type": "string"},
        "gaps": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["score"],
    "additionalProperties": False,
}


def build_user_prompt(
    *, title: str, company: str, jd_text: str | None, profile: CandidateProfile
) -> str:
    """组装评分用的用户提示词（纯函数，便于测试与版本化）。"""
    jd_section = (jd_text or "").strip() or "（未抓到岗位描述，仅凭标题判断，请相应降低置信度）"
    keywords = "、".join(profile.keywords) or "（未指定）"
    directions = "、".join(profile.directions) or "（未指定）"
    return f"""## 候选人画像
- 目标关键词：{keywords}
- 目标方向：{directions}
- 背景摘要：{profile.summary or "（无）"}

## 岗位信息
- 公司：{company}
- 岗位：{title}
- 岗位描述：
{jd_section}

请给出评分与理由。"""


__all__ = [
    "PROMPT_VERSION",
    "SCORE_SCHEMA",
    "SYSTEM_PROMPT",
    "CandidateProfile",
    "ScoreCard",
    "ScoringError",
    "build_user_prompt",
]
