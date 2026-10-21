"""settings 切片的 HTTP 面 —— LLM 配置的读写与探测。

三条行为约定（都源自旧界面的教训）：

1. **密钥只回显掩码**：响应里给 `masked_key`，绝不回传完整 key；
2. **空 key = 不改**：界面只回显掩码，所以提交空值必须理解为「保留原 key」，
   否则用户每改一次模型就把自己的 key 抹掉；
3. **校验失败就地返回 422 并带上原文**：不是甩一个 500，也不是静默丢弃。
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from hunter1.application.ports import LLMProvider
from hunter1.domain.llm import LLMError, describe_llm_error
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


def _failure_text(exc: Exception) -> str:
    """探测失败的对外文案。

    `LLMError` 走**可行动的中文**（见 `domain.llm.describe_llm_error`）—— 界面此前直接
    显示 `LLMError: transport_failed`，用户不知道下一步该做什么。其它异常保留
    「类型名: 消息」的形状：那些通常是环境/实现问题（代理、证书、库的怪状态），
    原文对排查更有用，而文案本身会经过 `_redact` 抹掉密钥。
    """
    if isinstance(exc, LLMError):
        return describe_llm_error(exc)
    return f"{type(exc).__name__}: {exc}"


def _origin(url: str) -> tuple[str, str, int | None]:
    """URL 的 (scheme, host, port)；端口按协议补默认值，便于比较。"""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, (parts.hostname or "").lower(), port


def _same_origin(left: str, right: str) -> bool:
    """两个 base_url 是否指向同一个服务（scheme + host + port）。

    只比 host 不够：`http://host` 与 `https://host`、`:8080` 与默认端口都是
    不同的信任边界。旧 Key 只能回填到**同一个 origin**，否则一次改地址就会把
    密钥发给另一台服务器。
    """
    return _origin(left) == _origin(right)


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
            # 破损数据里没有「原值」可保留 —— 旧 key 只能当空，让用户重填。
            existing = None
        incoming = form.api_key.strip()
        try:
            # 先校验地址本身：非法 URL 要给出字段级原因，而不是被「换了端点」
            # 这条策略提前拦成另一种错误。
            candidate = LLMSettings(
                base_url=form.base_url,
                model=form.model,
                api_key=incoming,
            )
        except ValidationError as exc:
            # 就地回显错误原因 —— 用户要知道哪儿错了，而不是一个 500。
            raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
        # 空 key = 「不改」**仅限端点没变**。改了 scheme/host/port 还沿用旧 key，
        # 等于把密钥悄悄发给另一台服务器。
        reuse_saved = (
            existing is not None
            and existing.api_key != ""
            and incoming == ""
            and _same_origin(existing.base_url, form.base_url)
        )
        if existing is not None and existing.api_key and incoming == "" and not reuse_saved:
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "loc": ["body", "api_key"],
                        "msg": "端点地址已改变：请重新输入 API Key（旧 Key 不会发送到新地址）",
                        "type": "value_error",
                    }
                ],
            )
        if reuse_saved and existing is not None:
            candidate = LLMSettings(
                base_url=form.base_url,
                model=form.model,
                api_key=existing.api_key,
            )
        warning = plaintext_warning(candidate.base_url)
        if warning:
            # 公网明文 http 不再是「提示后照存」：Key 与对话内容会明文过网。
            # 本机 / 内网 http 仍在 `plaintext_warning` 的白名单里，不受影响。
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "loc": ["body", "base_url"],
                        "msg": warning,
                        "type": "value_error",
                    }
                ],
            )
        store.save_llm(candidate)
        return _view(candidate)

    @router.post("/settings/test", summary="连通性探测（真发一次最小请求）")
    def test_connection(form: SettingsForm | None = None) -> ConnectionTestResponse:
        """表单给了值就探测**表单这一份**，没给才探测已保存的配置。

        为什么要有请求体：用户改了输入框再点「测试连接」，他以为验的是眼前这份；
        原先端点不带请求体，探测的却是**服务端已保存**的配置 —— 于是可能先看到
        「连接成功」，保存下去才发现另一份根本连不上（或反过来）。请求体可选是为了
        兼容不带体的调用（curl / 旧前端）：那时退回原行为。

        `api_key` 留空 = 「不改」：界面只回显掩码、读不到原值，所以用它探测时得拿
        已保存的那把钥匙 —— 与 PUT 的同名字段同一约定。
        """
        broken: str | None = None
        try:
            settings = store.get_llm()
        except ValueError as exc:
            # 保存的配置不合法（数据损坏 / 旧版本遗留）。**不能 500** —— 与 GET/PUT 同一课：
            # 配置页是用户唯一的自救入口，探测也不例外（此前这里裸调 `get_llm()`，
            # 损坏时 `ValueError` 穿透成 500，而前端只显示一句「探测失败」，把原因吞掉）。
            #
            # 但也不能在这里无条件返回：那会让「表单给了值就探测表单这一份」的承诺失效
            # —— 用户改好输入框再点「测试连接」，收到的却是「已保存的配置不可用」，
            # 于是他会以为**自己新填的**那份有问题（正是这条注释下面那个 docstring
            # 要避免的事）。所以记下原因、继续往下走，由下面按「有没有表单」分派。
            settings = None
            broken = str(exc)
        if form is not None:
            incoming = form.api_key.strip()
            try:
                candidate = LLMSettings(base_url=form.base_url, model=form.model, api_key=incoming)
            except ValidationError as exc:
                # 与 PUT 同一形状：填的这份不合法 → 422 + 字段级原因。
                raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
            reuse_saved = (
                settings is not None
                and settings.api_key != ""
                and incoming == ""
                and _same_origin(settings.base_url, form.base_url)
            )
            if not incoming and settings is None:
                # 空 key = 「用已保存的那把钥匙」，可这把钥匙读不出来（配置坏了）。
                # 这时必须说清楚**缺 key**，而不是笼统地怪「已保存的配置」。
                return ConnectionTestResponse(
                    ok=False,
                    message="已保存的配置不可用，表单里也没填 API Key —— 请填好 Key 再测试。",
                )
            if settings is not None and settings.api_key and incoming == "" and not reuse_saved:
                return ConnectionTestResponse(
                    ok=False,
                    message="端点地址已改变：请重新输入 API Key 后再测试",
                )
            if reuse_saved and settings is not None:
                candidate = LLMSettings(
                    base_url=form.base_url, model=form.model, api_key=settings.api_key
                )
            warning = plaintext_warning(candidate.base_url)
            if warning:
                return ConnectionTestResponse(ok=False, message=warning)
            settings = candidate
        elif broken is not None:
            return ConnectionTestResponse(
                ok=False,
                message=f"已保存的配置不可用，请重新填写并保存后再测试：{broken}",
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
                message=_redact(_failure_text(exc), settings),
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
