"""scoring 切片的 HTTP 面 —— 纯 JSON API（`/api` 前缀由组装处添加）。

组装处调用 `build_router(store=..., llm_factory=..., profile_provider=...)` 注入依赖；
切片不 import web。

两条设计约束：

1. **画像用 provider 而不是实例** —— 与 `llm_factory` 同一个理由：画像是运行时可变的
   （用户在「配置」页改写），启动时取一次会让改画像必须重启才生效。
2. **始终挂载** —— 路由不因「画像尚未配置」而消失。若改成「没配就不挂载」，前端照
   契约发出的 `POST /api/scoring/{job_id}` 会落进 SPA 回落的 `GET /{path:path}`，
   收到 **405 Method Not Allowed** —— 一个与真实原因（缺画像）毫无关系的错误，
   排查成本极高。（实测过：这正是迁移完成后成品里的实际行为。）

错误语义：

| 状态 | 含义 |
|---|---|
| 409 | 画像未配置 —— 请求没毛病，是服务端状态未就绪，`detail` 给出修复指引 |
| 404 | 岗位不存在 |
| 422 | 请求体不合法（画像为空）或模型侧失败（依赖给不出可用结果） |

注意 422 不吞非契约异常：`LLMProvider` 实现约定失败抛 `LLMError`；抛别的说明是
实现 bug，应当响亮地失败（500），而不是伪装成「模型不可用」（见测试
`TestUnexpectedFailure`）。同理，此处**不**捕获宽泛的 `RuntimeError`。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from hunter1.application.ports import LLMProvider
from hunter1.slices.scoring.models import CandidateProfile, ScoringError
from hunter1.slices.scoring.schemas import ProfileForm, ProfileView, ScoreView
from hunter1.slices.scoring.service import score_job
from hunter1.slices.scoring.store import ScoreStore

#: 未配置画像时的对外说明 —— 写清楚「缺什么」与「去哪里补」。
PROFILE_MISSING_DETAIL = (
    "候选人画像未配置：请先在「配置」页填写画像（关键词 / 方向 / 背景摘要），再发起评分"
)


def build_router(
    *,
    store: ScoreStore,
    llm_factory: Callable[[], LLMProvider],
    profile_provider: Callable[[], CandidateProfile | None],
) -> APIRouter:
    """构造 scoring 的 APIRouter（依赖由组装处注入）。

    `llm_factory` / `profile_provider` 都是**每次请求取当前值**：模型配置与画像
    都属于「用户改了要立刻生效」的运行时可变状态。
    """
    router = APIRouter()

    @router.get("/scoring/profile", summary="读候选人画像（未配置时为 null）")
    def read_profile() -> ProfileView:
        return ProfileView(profile=profile_provider())

    @router.put("/scoring/profile", summary="保存候选人画像")
    def save_profile(form: ProfileForm) -> ProfileView:
        try:
            profile = form.to_profile()
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"画像至少要有一项信号（关键词 / 方向 / 背景摘要）：{exc}",
            ) from exc
        store.save_profile(profile)
        return ProfileView(profile=profile)

    @router.post("/scoring/{job_id}", summary="给一个岗位评分并写回")
    def score(job_id: str) -> ScoreView:
        profile = profile_provider()
        if profile is None:
            raise HTTPException(status_code=409, detail=PROFILE_MISSING_DETAIL)

        job = store.load(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"岗位不存在：{job_id}")

        try:
            card = score_job(job=job, profile=profile, llm=llm_factory())
        except ScoringError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        store.save_score(job_id, card.score)
        return ScoreView(
            job_id=job_id,
            score=card.score,
            summary=card.summary,
            model=card.model,
            prompt_version=card.prompt_version,
        )

    return router


__all__ = ["PROFILE_MISSING_DETAIL", "build_router"]
