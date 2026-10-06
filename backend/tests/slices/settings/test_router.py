"""settings 切片测试 —— 真 SQLite + TestClient + 假 LLM，全程离线。

重点锁三条行为约定（都源自旧界面的教训）：
密钥只回显掩码、空 key = 不改、校验失败就地给原因。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hunter1.domain.llm import LLMResponse
from hunter1.domain.settings import LLMSettings
from hunter1.platform.db import Database
from hunter1.slices.settings.router import build_router
from hunter1.slices.settings.store import SettingsStore


class FakeLLM:
    def __init__(self, *, boom: bool = False) -> None:
        self.boom = boom

    def complete(self, **_kw: Any) -> LLMResponse:
        if self.boom:
            raise RuntimeError("endpoint unreachable")
        return LLMResponse(content="可用", model="fake-model")


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "settings.db")
    database.initialize()
    return database


def _client(db: Database, llm: FakeLLM | None = None) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(
        build_router(
            store=SettingsStore(db),
            llm_factory=lambda _s: llm or FakeLLM(),  # type: ignore[arg-type,return-value]
        ),
        prefix="/api",
    )
    with TestClient(app) as test_client:
        yield test_client


FORM = {
    "base_url": "https://api.example.com/v1",
    "model": "m",
    "api_key": "sk-secret-1234",
}


class TestReadSettings:
    def test_null_when_never_configured(self, db: Database) -> None:
        for client in _client(db):
            assert client.get("/api/settings").json() is None

    def test_returns_masked_key_only(self, db: Database) -> None:
        """密钥只给掩码 —— 完整 key 绝不出现在响应里。"""
        for client in _client(db):
            client.put("/api/settings", json=FORM)
            payload = client.get("/api/settings").json()
            assert payload["configured"] is True
            assert payload["model"] == "m"
            assert payload["masked_key"] != "sk-secret-1234"
            assert "sk-secret-1234" not in str(payload)


class TestSaveSettings:
    def test_roundtrip(self, db: Database) -> None:
        for client in _client(db):
            response = client.put("/api/settings", json=FORM)
            assert response.status_code == 200
            assert response.json()["configured"] is True
            assert db.settings().get_llm() is not None

    def test_blank_key_keeps_existing(self, db: Database) -> None:
        """界面只回显掩码 —— 空 key 必须理解为「不改」，不能把 key 抹掉。"""
        for client in _client(db):
            client.put("/api/settings", json=FORM)
            client.put(
                "/api/settings",
                json={"base_url": "https://api.example.com/v1", "model": "m2", "api_key": ""},
            )
            saved = db.settings().get_llm()
            assert saved is not None
            assert saved.api_key == "sk-secret-1234"
            assert saved.model == "m2"

    def test_new_key_overrides(self, db: Database) -> None:
        for client in _client(db):
            client.put("/api/settings", json=FORM)
            client.put("/api/settings", json={**FORM, "api_key": "sk-new"})
            saved = db.settings().get_llm()
            assert saved is not None and saved.api_key == "sk-new"

    def test_invalid_base_url_is_422_with_reason(self, db: Database) -> None:
        """校验失败就地给原因，不是 500、不是静默丢弃。"""
        for client in _client(db):
            response = client.put("/api/settings", json={**FORM, "base_url": "not-a-url"})
            assert response.status_code == 422
            assert "http(s)" in response.json()["detail"]
            assert db.settings().get_llm() is None  # 没有落库


class TestConnectionProbe:
    def test_success(self, db: Database) -> None:
        for client in _client(db):
            client.put("/api/settings", json=FORM)
            payload = client.post("/api/settings/test").json()
            assert payload["ok"] is True
            assert "fake-model" in payload["message"]

    def test_failure_is_reported_not_raised(self, db: Database) -> None:
        for client in _client(db, FakeLLM(boom=True)):
            client.put("/api/settings", json=FORM)
            payload = client.post("/api/settings/test").json()
            assert payload["ok"] is False
            assert "unreachable" in payload["message"]

    def test_without_config_explains(self, db: Database) -> None:
        for client in _client(db):
            payload = client.post("/api/settings/test").json()
            assert payload["ok"] is False
            assert "配置不完整" in payload["message"]


def test_api_key_input_must_not_leak_through_settings_view(db: Database) -> None:
    """掩码函数的形态与其他测试一致：前缀 + 末四位。"""
    settings = LLMSettings(**FORM)
    assert settings.masked_key() == "sk-…1234"
