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
| 409 | 服务端状态未就绪：画像未配置/不可用，或模型未配置（`detail` 给修复指引） |
| 404 | 岗位不存在（`GET` 的另一层含义：该岗位还没有评过分） |
| 422 | 请求体不合法（画像为空）或模型侧失败（依赖给不出可用结果） |

409 的两种来源共用一个码是**刻意**的：界面上的动作是同一个（去「配置」页补齐），
而 `detail` 已经能分辨。新增第三种来源时同样要更新这张表。

注意 422 不吞非契约异常：`LLMProvider` 实现约定失败抛 `LLMError`；抛别的说明是
实现 bug，应当响亮地失败（500），而不是伪装成「模型不可用」（见测试
`TestUnexpectedFailure`）。同理，此处**不**捕获宽泛的 `RuntimeError`。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from hunter1.application.ports import LLMProvider
from hunter1.slices.scoring.models import (
    MAX_PROFILE_DIRECTIONS,
    MAX_PROFILE_ITEM_CHARS,
    MAX_PROFILE_KEYWORDS,
    MAX_PROFILE_SUMMARY_CHARS,
    CandidateProfile,
    ScoringError,
)
from hunter1.slices.scoring.schemas import ProfileForm, ProfileView, ScoreView
from hunter1.slices.scoring.service import score_job
from hunter1.slices.scoring.store import ScoreStore

#: 未配置画像时的对外说明 —— 写清楚「缺什么」与「去哪里补」。
PROFILE_MISSING_DETAIL = (
    "候选人画像未配置：请先在「配置」页填写画像（关键词 / 方向 / 背景摘要），再发起评分"
)

#: 画像不合法的通用说明。带上具体上限，用户才知道该改什么；
#: 只说「至少要有一项信号」在「超长」的情况下是**误导**（信号有，是太多）。
PROFILE_INVALID_DETAIL = (
    "画像不合法：至少要有一项信号（关键词 / 方向 / 背景摘要），且不得超出上限"
    f"（关键词 {MAX_PROFILE_KEYWORDS} 条、方向 {MAX_PROFILE_DIRECTIONS} 条、"
    f"单条 {MAX_PROFILE_ITEM_CHARS} 字符、摘要 {MAX_PROFILE_SUMMARY_CHARS} 字符）"
)


def _now() -> datetime:
    """当前时刻（UTC）。注入 `clock` 是为了让测试固定时间。"""
    return datetime.now(UTC)


def build_router(
    *,
    store: ScoreStore,
    llm_factory: Callable[[], LLMProvider],
    profile_provider: Callable[[], CandidateProfile | None],
    clock: Callable[[], datetime] | None = None,
) -> APIRouter:
    """构造 scoring 的 APIRouter（依赖由组装处注入）。

    `llm_factory` / `profile_provider` 都是**每次请求取当前值**：模型配置与画像
    都属于「用户改了要立刻生效」的运行时可变状态。
    """
    router = APIRouter()
    now = clock or _now

    def _load_profile_or_409() -> CandidateProfile | None:
        """取当前画像；存储损坏时报 409 + 原因（**不是 500**）。

        与 `read_profile` 的处理刻意不同：GET 要能打开表单去修（200 + warning），
        评分则必须明确拒绝（409）—— 状态未就绪就不该往下走。两处共用「损坏」
        这个概念，但对外行为按各自职责取舍。
        """
        try:
            return profile_provider()
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"已保存的画像不可用，请到「配置」页重新填写：{exc}",
            ) from exc

    @router.get("/scoring/profile", summary="读候选人画像（未配置时为 null）")
    def read_profile() -> ProfileView:
        try:
            profile = profile_provider()
        except ValueError as exc:
            # 存储里的画像不合法。**不能 500** —— 那会让用户连配置页都打不开，
            # 拿不到表单就永远修不了这条数据。也不能静默当成「没配过」——
            # 那会掩盖损坏，用户填过的内容无声消失。
            # 所以：空画像（表单可用）+ 可读原因（用户知道要去重填）。
            return ProfileView(profile=None, warning=f"已保存的画像不可用，请重新填写：{exc}")
        return ProfileView(profile=profile)

    @router.put("/scoring/profile", summary="保存候选人画像")
    def save_profile(form: ProfileForm) -> ProfileView:
        try:
            profile = form.to_profile()
        except (ValidationError, ValueError) as exc:
            # 校验必须在写入之前 —— 被拒的请求不许动已存画像
            raise HTTPException(
                status_code=422,
                detail=f"{PROFILE_INVALID_DETAIL}。具体原因：{exc}",
            ) from exc
        store.save_profile(profile)
        return ProfileView(profile=profile)

    @router.post("/scoring/{job_id}", summary="给一个岗位评分并写回")
    def score(job_id: str) -> ScoreView:
        profile = _load_profile_or_409()
        if profile is None:
            raise HTTPException(status_code=409, detail=PROFILE_MISSING_DETAIL)

        job = store.load(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"岗位不存在：{job_id}")

        llm = llm_factory()
        try:
            card = score_job(job=job, profile=profile, llm=llm)
        except ScoringError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            # 客户端是每请求新建的 —— 用完即释放，别把连接池攒在进程里
            llm.close()

        scored_at = now()
        if (
            store.save_score(
                job_id,
                card.score,
                model=card.model,
                prompt_version=card.prompt_version,
                scored_at=scored_at,
                summary=card.summary,
                advantages=card.advantages,
                gaps=card.gaps,
            )
            is None
        ):
            # 窄竞态：上面 load 到了、写分前岗位被删。store.save_score 的契约就是
            # 「岗位不存在时返回 None，调用方据此报 404」—— 不接就会返回 200
            # 声称已写分。
            raise HTTPException(status_code=404, detail=f"岗位不存在：{job_id}")
        return ScoreView(
            job_id=job_id,
            score=card.score,
            summary=card.summary,
            advantages=card.advantages,
            gaps=card.gaps,
            model=card.model,
            prompt_version=card.prompt_version,
            scored_at=scored_at,
        )

    @router.get("/scoring/{job_id}", summary="读已存下的评分（未评过为 404）")
    def read_score(job_id: str) -> ScoreView:
        """把上一次评分的结果读回来。

        为什么要有这条：模型算出来的优势 / 差距 / 摘要原先只在 POST 的响应里闪一下
        就没了，刷新页面即无据可查 —— 写下来的东西必须有读回去的路，否则「落库」
        只是自欺。响应形状与 POST 相同，前端两处共用一套渲染。
        """
        job = store.load(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"岗位不存在：{job_id}")
        if job.match_score is None:
            # 「还没评过」用 404 + 可读原因，而不是返回 null：本端点的语义是
            # 「读一份已存在的评分」，没有就是不满足前置条件。与 `GET /scoring/profile`
            # 的取舍刻意不同 —— 那边的 `null` 是**正常初始态**（界面据此渲染空表单）。
            raise HTTPException(status_code=404, detail=f"该岗位还没有评过分：{job_id}")
        return ScoreView(
            job_id=job.id,
            score=job.match_score,
            summary=job.score_summary,
            advantages=job.score_advantages,
            gaps=job.score_gaps,
            model=job.score_model,
            prompt_version=job.score_prompt_version,
            scored_at=job.scored_at,
        )

    return router


__all__ = ["PROFILE_MISSING_DETAIL", "build_router"]
