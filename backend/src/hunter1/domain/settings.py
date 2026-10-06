"""LLM 配置 —— 纯模型与规则，无 IO。

「任意 OpenAI 兼容端点」这件事在界面上就是一个表单：`base_url` / `model` /
`api_key`。把「什么算合法、什么算填全了」收在这里，界面与用例就不必各判一遍。

关于密钥存储的取舍（诚实记录）：本工具是**本地单用户**，密钥存在本地 SQLite
里，明文。这不是疏忽 —— 单机场景下系统钥匙串的方案要引入平台特有依赖，与
「零托管、跨平台、无外部运行时」的硬约束冲突。因此改为：界面只回显掩码
（`masked_key`），日志与页面都不输出完整密钥；README 里写明这一点。
"""

from __future__ import annotations

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


__all__ = ["LLMSettings"]
