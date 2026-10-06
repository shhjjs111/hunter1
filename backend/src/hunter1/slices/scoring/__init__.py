"""scoring 切片 —— 匹配评分（提示词与代码共置）。

公开面（其他切片只允许从这里 import 符号）：

- `score_job`：评分用例
- `CandidateProfile` / `ScoreCard` / `ScoringError`：领域模型
- `ProfileForm` / `ProfileView` / `ScoreView`：HTTP 形状（契约源头）
- `PROMPT_VERSION` / `build_user_prompt`：提示词（换代只动 `prompts.py`）
- `ScoreStore` / `build_router`

详见 `SLICE.md`。
"""

from hunter1.slices.scoring.models import CandidateProfile, ScoreCard, ScoringError
from hunter1.slices.scoring.prompts import (
    PROMPT_VERSION,
    SCORE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from hunter1.slices.scoring.router import build_router
from hunter1.slices.scoring.schemas import ProfileForm, ProfileView, ScoreView
from hunter1.slices.scoring.service import score_job
from hunter1.slices.scoring.store import PROFILE_KEY, ScoreStore

__all__ = [
    "PROFILE_KEY",
    "PROMPT_VERSION",
    "SCORE_SCHEMA",
    "SYSTEM_PROMPT",
    "CandidateProfile",
    "ProfileForm",
    "ProfileView",
    "ScoreCard",
    "ScoreStore",
    "ScoreView",
    "ScoringError",
    "build_router",
    "build_user_prompt",
    "score_job",
]
