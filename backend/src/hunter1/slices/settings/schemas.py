"""settings 切片的 API 模型 —— 跨端契约的**唯一事实来源**。

这些 Pydantic 模型经 OpenAPI 快照（`contracts/openapi.json`）流向
前端类型（`frontend/src/shared/api/schema.d.ts`）。改这里 = 改契约：
改完跑 `bash scripts/contracts.sh` 重新导出并提交快照。

（原先这些模型内联在 `router.py` 里 —— 本仓库其余切片都按 AGENTS.md 的形制
把 API 模型放在 `schemas.py`，这两个切片是遗漏。搬运不改任何字段定义，因此
契约快照应零漂移；`contracts.sh --check` 是这条断言的证据。）
"""

from __future__ import annotations

from pydantic import BaseModel


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
    #: 非致命提示（如明文 http:// 指向公网）。与 scoring 的 ProfileView 同形：
    #: 值仍可用，但用户该知道这层风险 —— 静默接受等于把风险藏起来。
    warning: str | None = None


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


__all__ = ["ConnectionTestResponse", "SettingsForm", "SettingsView"]
