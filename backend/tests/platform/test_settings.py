"""配置仓储测试 —— LLM 配置的读写往返。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hunter1.domain.settings import LLMSettings
from hunter1.platform.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "settings.db")
    database.initialize()
    return database


def _settings(**overrides: object) -> LLMSettings:
    base: dict[str, object] = {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "sk-abc123456789",
    }
    base.update(overrides)
    return LLMSettings(**base)  # type: ignore[arg-type]


class TestSettingsRepository:
    def test_missing_settings_returns_none(self, db: Database) -> None:
        assert db.settings().get_llm() is None

    def test_roundtrip(self, db: Database) -> None:
        repo = db.settings()
        saved = _settings()
        repo.save_llm(saved)
        assert repo.get_llm() == saved

    def test_legacy_removed_fields_are_tolerated(self, db: Database) -> None:
        """旧版本存下的配置可能带已移除的字段（temperature / max_tokens）。

        `LLMSettings` 是 `extra="forbid"`：若不先剔除这两个键，升级后读旧库
        会直接把配置判成「损坏」，用户白挨一次惊吓。这里锁住「静默丢弃、不算坏」。
        """
        db.settings().set_raw(
            "llm",
            {
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "api_key": "sk-abc123456789",
                "temperature": 0.7,
                "max_tokens": 1200,
            },
        )
        loaded = db.settings().get_llm()
        assert loaded is not None and loaded.model == "deepseek-chat"

    def test_save_overwrites(self, db: Database) -> None:
        repo = db.settings()
        repo.save_llm(_settings(model="deepseek-chat"))
        repo.save_llm(_settings(model="deepseek-reasoner"))
        loaded = repo.get_llm()
        assert loaded is not None and loaded.model == "deepseek-reasoner"

    def test_survives_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "persist.db"
        first = Database(path)
        first.initialize()
        first.settings().save_llm(_settings())
        first.dispose()

        reopened = Database(path)
        reopened.initialize()
        assert reopened.settings().get_llm() is not None

    def test_corrupt_row_is_not_silently_swallowed(self, db: Database) -> None:
        """存进去的东西读不出来时要报错 —— 静默返回 None 会让人以为「没配置」。"""
        db.settings().set_raw("llm", {"unexpected": "shape"})
        with pytest.raises(ValueError):
            db.settings().get_llm()

    def test_raw_roundtrip(self, db: Database) -> None:
        """底层键值读写是通用能力，供后续偏好项复用。"""
        repo = db.settings()
        assert repo.get_raw("missing") is None
        repo.set_raw("prefs", {"page_size": 20})
        assert repo.get_raw("prefs") == {"page_size": 20}


def _write_raw(db: Database, key: str, value: object) -> None:
    """绕过 `set_raw` 直接写存储 —— 模拟手工改库 / 外部工具写入的损坏值。

    `set_raw` 自己会 `dict(value)`，非对象值在写入时就抛错，造不出这种形状；
    而「配置损坏」恰恰多半来自应用之外的写入（改库、旧版本遗留、同步工具）。
    """
    import json

    from sqlalchemy import text

    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT OR REPLACE INTO settings (key, value) VALUES (:k, :v)"),
            {"k": key, "v": json.dumps(value)},
        )


class TestNonObjectStoredValueIsCorruptionNotCrash:
    """存储值不是对象时，必须归到「配置损坏」的词汇（ValueError），不能抛 TypeError。

    为什么：`except ValueError` 是全仓统一的「配置损坏」守卫 —— settings 路由的
    GET / PUT / 连通性探测、scoring 的画像读取都靠它。`dict(123)` 抛的 **TypeError
    会绕过全部守卫直穿成 500**，与「损坏必须可修复、不能自锁」的不变量冲突。

    （缺口由独立复核发现，探针实测 int / list 两种形状确实抛 TypeError。）
    """

    @pytest.mark.parametrize("value", [123, [1, 2], "abc"])
    def test_non_object_value_raises_valueerror(self, db: Database, value: object) -> None:
        _write_raw(db, "llm", value)
        with pytest.raises(ValueError):
            db.settings().get_raw("llm")

    def test_get_llm_on_non_object_is_valueerror(self, db: Database) -> None:
        _write_raw(db, "llm", 123)
        with pytest.raises(ValueError):
            db.settings().get_llm()
