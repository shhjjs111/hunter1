"""settings 切片的存取门面 —— 薄委托 platform.db 的配置仓储。"""

from __future__ import annotations

from hunter1.domain.settings import LLMSettings
from hunter1.platform.db import Database


class SettingsStore:
    """LLM 配置的读写。

    `get_llm()` 在读不出合法配置时**抛错而不是返回 None**（见 platform.db.settings）——
    「没配过」与「配过但坏了」是两件事，界面要能分辨。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_llm(self) -> LLMSettings | None:
        return self._db.settings().get_llm()

    def save_llm(self, settings: LLMSettings) -> None:
        self._db.settings().save_llm(settings)


__all__ = ["SettingsStore"]
