"""settings 切片的 HTTP 面 —— LLM 配置的读写与探测。

三条行为约定（都源自旧界面的教训）：

1. **密钥只回显掩码**：响应里给 `masked_key`，绝不回传完整 key；
2. **空 key = 不改**：界面只回显掩码，所以提交空值必须理解为「保留原 key」，
   否则用户每改一次模型就把自己的 key 抹掉；
3. **校验失败就地返回 422 并带上原文**：不是甩一个 500，也不是静默丢弃。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from hunter1.application.ports import LLMProvider
from hunter1.domain.settings import LLMSettings
from hunter1.slices.settings.store import SettingsStore


class SettingsForm(BaseModel):
    """配置页提交的表单。`api_key` 留空表示「不改」。"""

    base_url: str
    model: str
    api_key: str = ""
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)


class SettingsView(BaseModel):
    """回给界面的配置（密钥只给掩码）。"""

    base_url: str
    model: str
    masked_key: str
    temperature: float | None = None
    max_tokens: int | None = None
    configured: bool


def _view(settings: LLMSettings) -> SettingsView:
    return SettingsView(
        base_url=settings.base_url,
        model=settings.model,
        masked_key=settings.masked_key(),
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        configured=settings.is_configured,
    )


def _readable(exc: ValidationError) -> str:
    """把 pydantic 报错压成一句人能读的话。"""
    errors = exc.errors()
    if not errors:
        return str(exc)
    return str(errors[0].get("msg", exc)).removeprefix("Value error, ")


def build_router(
    *,
    store: SettingsStore,
    llm_factory: Callable[[LLMSettings], LLMProvider],
) -> APIRouter:
    """构造 settings 的 APIRouter（依赖由组装处注入）。"""
    router = APIRouter()

    @router.get("/settings", response_model=SettingsView | None, summary="读取 LLM 配置")
    def read_settings() -> SettingsView | None:
        settings = store.get_llm()
        return _view(settings) if settings is not None else None

    @router.put("/settings", response_model=SettingsView, summary="保存 LLM 配置")
    def save_settings(form: SettingsForm) -> SettingsView:
        existing = store.get_llm()
        # 空 key = 「不改」而不是「清空」（界面只回显掩码，读不到原值）
        key = form.api_key.strip() or (existing.api_key if existing else "")
        try:
            candidate = LLMSettings(
                base_url=form.base_url,
                model=form.model,
                api_key=key,
                temperature=form.temperature,
                max_tokens=form.max_tokens,
            )
        except ValidationError as exc:
            # 就地回显错误原因 —— 用户要知道哪儿错了，而不是一个 500
            raise HTTPException(status_code=422, detail=_readable(exc)) from exc
        store.save_llm(candidate)
        return _view(candidate)

    @router.post("/settings/test", summary="连通性探测（真发一次最小请求）")
    def test_connection() -> dict[str, str]:
        settings = store.get_llm()
        if settings is None or not settings.is_configured:
            return {"ok": "0", "message": "配置不完整：base_url / 模型 / API Key 都要填。"}
        try:
            llm = llm_factory(settings)
            response = llm.complete(
                system_prompt="你是连通性测试助手。",
                user_prompt="只回复两个字：可用",
                max_tokens=16,
            )
        except Exception as exc:
            return {"ok": "0", "message": f"{type(exc).__name__}: {exc}"}
        return {"ok": "1", "message": f"连接成功，模型 {response.model or settings.model} 已应答。"}

    return router


__all__ = ["SettingsForm", "SettingsView", "build_router"]
