"""配置仓储 —— SQLite 键值表，实现 application 层定义的端口。

分两层：
- `get_raw` / `set_raw`：通用键值读写（供未来的偏好项复用）；
- `get_llm` / `save_llm`：把 LLM 配置序列化进同一个键值表。

`get_llm` 读不出合法配置时**抛错而不是返回 None** —— 返回 None 会让界面显示
「还没配置」，而用户明明配置过，只是数据坏了。两者必须能分辨。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import delete

from hunter1.domain.settings import LLMSettings
from hunter1.platform.db.schema import SettingRow

if TYPE_CHECKING:
    from hunter1.platform.db.database import Database

LLM_KEY = "llm"

#: 已从 `LLMSettings` 移除的字段。旧版本存下的配置 JSON 里可能还留着它们，
#: 而 `LLMSettings` 是 `extra="forbid"` —— 不先剔除的话，升级后读旧库会直接把
#: 这份配置判成「损坏」，用户被挡在配置页外（虽然重填能自救，但这是白挨的
#: 一次惊吓）。这些键没有语义了，静默丢弃即可。
_LEGACY_LLM_FIELDS = frozenset({"temperature", "max_tokens"})


class SqliteSettingsRepository:
    """键值配置仓储。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    # ---- 通用键值 ----

    def get_raw(self, key: str) -> dict[str, Any] | None:
        with self._db.session() as session:
            row = session.get(SettingRow, key)
            if row is None:
                return None
            return dict(row.value or {})

    def set_raw(self, key: str, value: dict[str, Any]) -> None:
        with self._db.session() as session:
            row = session.get(SettingRow, key)
            if row is None:
                row = SettingRow(key=key)
                session.add(row)
            row.value = dict(value)
            session.commit()

    def delete_raw(self, key: str) -> None:
        with self._db.session() as session:
            session.execute(delete(SettingRow).where(SettingRow.key == key))
            session.commit()

    # ---- LLM 配置 ----

    def get_llm(self) -> LLMSettings | None:
        raw = self.get_raw(LLM_KEY)
        if raw is None:
            return None
        for legacy in _LEGACY_LLM_FIELDS:
            raw.pop(legacy, None)
        try:
            return LLMSettings.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"已保存的 LLM 配置不合法：{exc}") from exc

    def save_llm(self, settings: LLMSettings) -> None:
        self.set_raw(LLM_KEY, settings.model_dump())


__all__ = ["LLM_KEY", "SqliteSettingsRepository"]
