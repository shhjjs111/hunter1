"""评分的领域类型与规则 —— 纯模型，无 IO 依赖。

（从旧 `domain/matching.py` 迁入：本切片自己的模型不再住在共享 domain 里，
「一个切片 = 一个领地」由此在类型层面也成立。）
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# ---- 画像的输入上限 ----
#
# 这不是「性能优化」，是**成本与可用性的闸门**：画像会被原样拼进评分提示词
# （见 `prompts.build_user_prompt`），所以它的规模**直接等于**每次评分请求的
# 提示词规模。实测（上限生效前）：5000 个关键词 + 10 万字符摘要 → 提示词
# 119,085 字符，一次评分就撞厂商 max_tokens 上限或产生高额费用；失败信息还是
# 「模型报错」，与真实原因（画像太大）毫无关系。
#
# 约束落在**领域模型**而不是只在 API 线层：`build_user_prompt` 依赖「画像小到
# 可用」，那是领域真理 —— 放在这里，提示词函数在构造上就收不到超大画像。
#
# 取值对真实画像很宽松：50 个关键词、20 个方向、单条 100 字符、摘要 2000 字符。
MAX_PROFILE_KEYWORDS = 50
MAX_PROFILE_DIRECTIONS = 20
MAX_PROFILE_ITEM_CHARS = 100
MAX_PROFILE_SUMMARY_CHARS = 2000

#: 单个条目（关键词 / 方向）：先去空白，再限长
ProfileItem = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=MAX_PROFILE_ITEM_CHARS)
]


class ScoringError(RuntimeError):
    """评分过程失败（模型输出不可用 / 传输错误）。"""


class CandidateProfile(BaseModel):
    """候选人画像。至少要有一项信号，否则评分无从谈起。"""

    model_config = ConfigDict(extra="forbid")

    keywords: list[ProfileItem] = Field(default_factory=list, max_length=MAX_PROFILE_KEYWORDS)
    directions: list[ProfileItem] = Field(default_factory=list, max_length=MAX_PROFILE_DIRECTIONS)
    summary: str = Field(default="", max_length=MAX_PROFILE_SUMMARY_CHARS)

    @field_validator("summary")
    @classmethod
    def _strip_summary(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("keywords", "directions")
    @classmethod
    def _drop_blank_items(cls, value: list[str]) -> list[str]:
        """去掉全空白的条目。

        不然 `["", ""]` 能通过下面「至少要有一项信号」的检查 —— 画像实际是空的，
        却被当成「已配置」，评分照跑（提示词里写着「（未指定）」）。那等于让
        不变量说谎。
        """
        return [item for item in value if item]

    def model_post_init(self, _context: object) -> None:
        if not self.keywords and not self.directions and not self.summary:
            raise ValueError("candidate profile needs at least one of keywords/directions/summary")


class ScoreCard(BaseModel):
    """一次评分的结论。`score` 是唯一必填项 —— 模型给不出分就不算评过。"""

    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=100)
    advantages: str | None = None
    gaps: str | None = None
    summary: str | None = None
    model: str | None = None
    prompt_version: str | None = None


__all__ = [
    "MAX_PROFILE_DIRECTIONS",
    "MAX_PROFILE_ITEM_CHARS",
    "MAX_PROFILE_KEYWORDS",
    "MAX_PROFILE_SUMMARY_CHARS",
    "CandidateProfile",
    "ProfileItem",
    "ScoreCard",
    "ScoringError",
]
