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

    @field_validator("summary", mode="before")
    @classmethod
    def _strip_summary(cls, value: object) -> object:
        """先去首尾空白，**再**算长度上限（`mode="before"`）。

        `max_length` 是字段约束，跑在 after-validator 之前 —— 顺序反了的话，
        「恰好 2000 字符 + 一个尾换行」会因为那个换行被拒（长度按未剥离的原文算），
        用户的观感是「明明只有 2000 字却说我超了」。
        """
        return value.strip() if isinstance(value, str) else value

    @field_validator("keywords", "directions", mode="before")
    @classmethod
    def _drop_blank_items(cls, value: object) -> object:
        """去掉全空白的条目 —— **在算 maxItems 之前**（`mode="before"`）。

        空白条目既不是用户写下的信号，也不该占配额：顺序反了时
        `["AI"] + [""] * 50` 会因为「51 条超过 50 条上限」被拒，而拒绝理由
        （「关键词超上限」）与实际（用户只写了 1 个关键词）不符 —— 用户盯着自己那
        一个关键词，不知道该改什么。本校验器的本意（见下方「至少要有一项信号」）
        正是「全空白条目不算内容」，那就得在计数之前生效。
        """
        if not isinstance(value, list):
            return value
        return [item for item in value if not (isinstance(item, str) and not item.strip())]

    def model_post_init(self, _context: object) -> None:
        if not self.keywords and not self.directions and not self.summary:
            # 中文：这句话会经 `PROFILE_INVALID_DETAIL` 一直显示到配置页上。
            raise ValueError("画像至少要有一项信号（关键词 / 方向 / 背景摘要）")


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
