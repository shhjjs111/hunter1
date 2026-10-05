"""scoring 切片的 HTTP 面 —— 纯 JSON API（`/api` 前缀由组装处添加）。

组装处调用 `build_router(store=..., llm=...)` 注入依赖；切片不 import web。

错误语义：模型侧失败（`ScoringError`）映射为 **422**（请求没毛病，是被依赖的
服务给不出可用结果），与 404（岗位不存在）分得开 —— 界面据此给不同提示。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from hunter1.application.ports import LLMProvider
from hunter1.slices.scoring.models import CandidateProfile, ScoringError
from hunter1.slices.scoring.service import score_job
from hunter1.slices.scoring.store import ScoreStore


def build_router(
    *,
    store: ScoreStore,
    llm_factory: Callable[[], LLMProvider],
    profile: CandidateProfile,
) -> APIRouter:
    """构造 scoring 的 APIRouter（依赖由组装处注入）。

    `llm_factory` 而不是 llm 实例：模型配置是**运行时可变的**（用户在设置页改
    API Key / 换模型），每次请求取当前配置构造客户端，改了立刻生效。
    """
    router = APIRouter()

    @router.post("/scoring/{job_id}", summary="给一个岗位评分并写回")
    def score(job_id: str) -> dict[str, object]:
        job = store.load(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"岗位不存在：{job_id}")
        try:
            card = score_job(job=job, profile=profile, llm=llm_factory())
        except ScoringError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.save_score(job_id, card.score)
        return {
            "job_id": job_id,
            "score": card.score,
            "summary": card.summary,
            "model": card.model,
            "prompt_version": card.prompt_version,
        }

    return router


__all__ = ["build_router"]
