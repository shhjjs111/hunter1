"""LLM 配置 —— 纯模型与规则，无 IO。

「任意 OpenAI 兼容端点」这件事在界面上就是一个表单：`base_url` / `model` /
`api_key`。把「什么算合法、什么算填全了」收在这里，界面与用例就不必各判一遍。

关于密钥存储的取舍（诚实记录）：本工具是**本地单用户**，密钥存在本地 SQLite
里，明文。这不是疏忽 —— 单机场景下系统钥匙串的方案要引入平台特有依赖，与
「零托管、跨平台、无外部运行时」的硬约束冲突。因此改为：界面只回显掩码
（`masked_key`），日志与页面都不输出完整密钥；README 里写明这一点。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMSettings(_Config):
    """一份 OpenAI 兼容端点的配置。"""

    base_url: str
    model: str
    api_key: str = ""

    @field_validator("base_url")
    @classmethod
    def _valid_base_url(cls, value: str) -> str:
        cleaned = (value or "").strip().rstrip("/")
        parts = urlsplit(cleaned)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"base_url 必须是 http(s) 地址，收到 {value!r}")
        # 端口必须可解析：`https://api.example.com:notaport/v1` 的 netloc 非空，
        # 能通过上面的检查并**存进库**，却会在发出请求时抛 httpx.InvalidURL ——
        # 到那一步就只剩 500。在这里挡住，用户当场看到 422 与原因。
        try:
            _ = parts.port
        except ValueError as exc:
            raise ValueError(f"base_url 的端口不合法：{value!r}") from exc
        return cleaned

    @field_validator("model")
    @classmethod
    def _non_empty_model(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("model 不能为空")
        return cleaned

    @field_validator("api_key")
    @classmethod
    def _strip_key(cls, value: str) -> str:
        return (value or "").strip()

    @property
    def is_configured(self) -> bool:
        """是否足以发起一次调用（三项齐备）。"""
        return bool(self.base_url and self.model and self.api_key)

    def masked_key(self) -> str:
        """给界面回显用：只保留前缀与末四位。"""
        if not self.api_key:
            return "（未设置）"
        if len(self.api_key) <= 8:
            return "•" * len(self.api_key)
        return f"{self.api_key[:3]}…{self.api_key[-4:]}"


__all__ = ["LLMSettings", "plaintext_warning"]


def plaintext_warning(base_url: str) -> str | None:
    """`base_url` 是明文 `http://` 且指向**公网**时的提示；否则 None。

    本地/内网用 http 是**合理**的（Ollama、vLLM、公司内网网关），所以只提示、
    不拒绝 —— 拒绝会把最常见的自建场景挡在门外。但公网明文意味着 API Key 与
    全部对话内容以明文过网，用户至少要知道自己在冒什么险（此前整条链路一个字
    都不提）。
    """
    parts = urlsplit((base_url or "").strip())
    if parts.scheme != "http":
        return None
    host = (parts.hostname or "").lower()
    if not host or _is_internal_host(host):
        return None
    return (
        f"base_url 用的是明文 http://（{host} 不是本机或内网地址）："
        "API Key 与对话内容会以明文过网，建议改用 https://。"
    )


def _is_internal_host(host: str) -> bool:
    """本机 / 私有网段 / 内网单标签名 —— 这些用 http 不提示。"""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if host.endswith(".local"):
        return True
    if "." not in host and ":" not in host:
        # 单标签名（`ollama`、`gateway`、`llm-host`）：内网名，不是公网域名
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local
