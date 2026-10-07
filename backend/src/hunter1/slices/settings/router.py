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
from pydantic import BaseModel, ValidationError

from hunter1.application.ports import LLMProvider
from hunter1.domain.settings import LLMSettings
from hunter1.slices.settings.store import SettingsStore


class SettingsForm(BaseModel):
    """配置页提交的表单。`api_key` 留空表示「不改」。"""

    base_url: str
    model: str
    api_key: str = ""


class SettingsView(BaseModel):
    """回给界面的配置（密钥只给掩码）。

    字段都带默认值，是为了容纳**配置已损坏**这一形状：此时没有任何可信值可回显，
    给一组空值 + `broken=True`，让表单直接空白可填（用户重填即可自救）。
    正常路径由 `_view()` 唯一构造，字段一定齐全。
    """

    base_url: str = ""
    model: str = ""
    masked_key: str = ""
    configured: bool = False
    #: 已保存的配置不合法（数据损坏 / 旧版本遗留）。界面据此提示「请重新填写」，
    #: 而不是把它当成「还没配过」——那会让用户填过的内容无声消失。
    broken: bool = False


def _view(settings: LLMSettings) -> SettingsView:
    return SettingsView(
        base_url=settings.base_url,
        model=settings.model,
        masked_key=settings.masked_key(),
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
        try:
            settings = store.get_llm()
        except ValueError:
            # 数据损坏 —— 必须给 200 + 标记，**不能 500**。
            #
            # `get_llm()` 抛错是对的（区分「没配过」与「配过但坏了」），但路由不接
            # 就变成 500，而配置页是用户**唯一**能重填覆盖的地方 —— 500 等于自锁，
            # 用户被自己的坏数据永久挡在门外。（scoring 切片对同一场景已按
            # 「GET 200 + warning」处理，理由写在那里；这里补同一课。）
            #
            # 也不能静默当成「没配过」：那会掩盖损坏，用户填过的内容无声消失。
            return SettingsView(broken=True)
        return _view(settings) if settings is not None else None

    @router.put("/settings", response_model=SettingsView, summary="保存 LLM 配置")
    def save_settings(form: SettingsForm) -> SettingsView:
        try:
            existing = store.get_llm()
        except ValueError:
            # 破损数据里没有「原值」可保留 —— 空 key 就只能当空，让用户重填。
            # 这里若让它抛出去，修复路径同样被堵死（PUT 也 500），与 GET 同一课。
            existing = None
        # 空 key = 「不改」而不是「清空」（界面只回显掩码，读不到原值）
        key = form.api_key.strip() or (existing.api_key if existing else "")
        try:
            candidate = LLMSettings(
                base_url=form.base_url,
                model=form.model,
                api_key=key,
            )
        except ValidationError as exc:
            # 就地回显错误原因 —— 用户要知道哪儿错了，而不是一个 500
            raise HTTPException(status_code=422, detail=_readable(exc)) from exc
        store.save_llm(candidate)
        return _view(candidate)

    @router.post("/settings/test", summary="连通性探测（真发一次最小请求）")
    def test_connection() -> ConnectionTestResponse:
        try:
            settings = store.get_llm()
        except ValueError as exc:
            # 保存的配置不合法（数据损坏 / 旧版本遗留）。**不能 500** —— 与 GET/PUT 同一课：
            # 配置页是用户唯一的自救入口，探测也不例外（此前这里裸调 `get_llm()`，
            # 损坏时 `ValueError` 穿透成 500，而前端只显示一句「探测失败」，把原因吞掉）。
            #
            # 保持端点的「报告形状」不变（与下面「配置不完整」同形：200 + `ok=False` +
            # 可读原因），而不是抛 409：前端 `useTestConnection` 在出错分支只显示
            # 「探测失败」，用一个统一的状态码换不回丢失的指引。
            return ConnectionTestResponse(
                ok=False,
                message=f"已保存的配置不可用，请重新填写并保存后再测试：{exc}",
            )
        if settings is None or not settings.is_configured:
            return ConnectionTestResponse(
                ok=False, message="配置不完整：base_url / 模型 / API Key 都要填。"
            )
        llm = llm_factory(settings)
        try:
            response = llm.complete(
                system_prompt="你是连通性测试助手。",
                user_prompt="只回复两个字：可用",
                max_tokens=16,
            )
        except Exception as exc:
            return ConnectionTestResponse(ok=False, message=f"{type(exc).__name__}: {exc}")
        finally:
            # 探测也是一次完整使用 —— 客户端每请求新建，用完释放
            llm.close()
        return ConnectionTestResponse(
            ok=True, message=f"连接成功，模型 {response.model or settings.model} 已应答。"
        )

    return router


class ConnectionTestResponse(BaseModel):
    """连通性探测的结果 —— **有类型的响应**，不是裸 dict。

    裸 `dict[str, str]` 会让 OpenAPI 退化成 `additionalProperties: {type: string}`
    （契约里看不出有什么字段），前端只能靠魔法值判断：`probe.data.ok === "1"`。
    本仓库其余 5 个切片所有端点都有类型，唯独这里漏了（AGENTS.md「响应要有类型」）。

    `ok` 用 **bool** 而不是 "1"/"0"：字符串状态码有两个毛病 —— 前端得写
    `=== "1"` 这种依赖约定的比较；契约里也说不清 `ok` 到底有哪些取值。
    """

    ok: bool
    message: str


__all__ = ["ConnectionTestResponse", "SettingsForm", "SettingsView", "build_router"]
