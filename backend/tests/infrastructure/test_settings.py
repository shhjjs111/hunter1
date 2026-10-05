"""配置仓储测试 —— LLM 配置的读写往返。

TDD：本文件先于实现编写，当前应为 RED。
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
        saved = _settings(temperature=0.3, max_tokens=1200)
        repo.save_llm(saved)
        assert repo.get_llm() == saved

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
