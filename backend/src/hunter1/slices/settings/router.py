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
from pydantic import ValidationError

from hunter1.application.ports import LLMProvider
from hunter1.domain.settings import LLMSettings, plaintext_warning
from hunter1.platform.text import redact_secret
from hunter1.slices.settings.schemas import (
    ConnectionTestResponse,
    SettingsForm,
    SettingsView,
)
from hunter1.slices.settings.store import SettingsStore


def _redact(message: str, settings: LLMSettings) -> str:
    """把错误文案里可能出现的 API Key 抹掉。

    底层 HTTP 客户端的异常会带上请求细节（有的厂商直接把 Authorization 回显在
    4xx 体里），而这段文案是要显示给用户、也可能是用户贴给维护者的 ——
    它绝不能含完整密钥。实现见 `platform.text.redact_secret`（与 LLM 客户端
    自身出错时的抹除共用同一份，避免两处措辞/规则分叉）。
    """
    return redact_secret(message, settings.api_key)


def _view(settings: LLMSettings) -> SettingsView:
    return SettingsView(
        base_url=settings.base_url,
        model=settings.model,
        masked_key=settings.masked_key(),
        configured=settings.is_configured,
        # 明文 http 指向公网 → 提示（不拒绝：本机/内网用 http 是合理的）
        warning=plaintext_warning(settings.base_url),
    )


def _validation_detail(exc: ValidationError) -> list[dict[str, object]]:
    """把 pydantic 报错转成 **FastAPI 同款**的 422 detail。

    契约里 422 的 `detail` 是 `ValidationError[]`（元素 `{loc, msg, type}`）。这里手工
    抛 422（表单本身合法、但拼出来的配置不合法）时必须给同一形状 —— 否则同一个
    端点上会出现两种 422 体，前端和契约都得写两套解析。此前是压成字符串，前端
    `features/settings/api.ts` 里那句「只接受字符串 detail，形状一变就退化」的
    注释就是被这件事逼出来的。

    `loc` 加 `body` 前缀是为了与 FastAPI 自己的请求校验错误对齐（它写成
    `["body","base_url"]`），界面要按字段提示时不用区分两种来源。
    """
    errors = exc.errors()
    if not errors:
        return [{"loc": ["body"], "msg": str(exc), "type": "value_error"}]
    return [
        {
            "loc": ["body", *(str(part) for part in error.get("loc", ()))],
            "msg": str(error.get("msg", exc)).removeprefix("Value error, "),
            "type": str(error.get("type", "value_error")),
        }
        for error in errors
    ]


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
            # 就地回显错误原因 —— 用户要知道哪儿错了，而不是一个 500。
            # 形状与 FastAPI 的请求校验 422 一致（见 `_validation_detail`）。
            raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
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
        llm = None
        try:
            # 工厂也在 try **之内**：它同样可能因配置里表达不出的原因炸掉
            # （客户端库对 URL 形态的限制、代理设置等）。放在外面时异常会穿透成
            # 500，前端只显示一句「探测失败」—— 与上面那条注释同一个教训。
            llm = llm_factory(settings)
            response = llm.complete(
                system_prompt="你是连通性测试助手。",
                user_prompt="只回复两个字：可用",
                max_tokens=16,
            )
        except Exception as exc:
            return ConnectionTestResponse(
                ok=False,
                message=_redact(f"{type(exc).__name__}: {exc}", settings),
            )
        finally:
            # 探测也是一次完整使用 —— 客户端每请求新建，用完释放。
            # `if llm is not None`：工厂自己抛错时没有可关的对象（裸调 `llm.close()`
            # 会在 finally 里抛 UnboundLocalError，把真正的失败原因盖掉）。
            if llm is not None:
                llm.close()
        return ConnectionTestResponse(
            ok=True, message=f"连接成功，模型 {response.model or settings.model} 已应答。"
        )

    return router


__all__ = ["build_router"]
