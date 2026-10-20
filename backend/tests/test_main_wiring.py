"""组装根 `main.py` 里几个小型接线函数的直接测试。

它们此前只被集成用例间接碰到（或完全没碰到），覆盖率闸门（96%）的余量因此
只剩 0.18%：随便加一段未测代码就会把 CI 卡红。这里补上直测，同时把它们的
「读不到就退化」语义钉住 —— 脱敏辅助函数绝不能因为配置损坏而抛异常。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import hunter1.main as main_module
from hunter1.domain.settings import LLMSettings


class _Settings:
    def __init__(self, value: LLMSettings | None = None, error: Exception | None = None) -> None:
        self._value = value
        self._error = error

    def get_llm(self) -> LLMSettings | None:
        if self._error is not None:
            raise self._error
        return self._value


def _context(settings: _Settings) -> main_module.AppContext:
    db = SimpleNamespace(settings=lambda: settings)
    return SimpleNamespace(db=db)  # type: ignore[return-value]


class TestCurrentApiKey:
    def test_returns_configured_key(self) -> None:
        cfg = LLMSettings(base_url="https://example.invalid/v1", model="m", api_key="dummy-key")
        assert main_module._current_api_key(_context(_Settings(cfg))) == "dummy-key"

    def test_unconfigured_is_empty(self) -> None:
        assert main_module._current_api_key(_context(_Settings(None))) == ""

    def test_corrupt_settings_degrade_to_empty(self) -> None:
        broken = _Settings(error=ValueError("corrupt"))
        assert main_module._current_api_key(_context(broken)) == ""


class TestDefaultLLMFactory:
    def test_builds_openai_compatible_client(self) -> None:
        from hunter1.platform.llm import OpenAICompatibleClient

        cfg = LLMSettings(base_url="https://example.invalid/v1", model="m", api_key="dummy-key")
        assert isinstance(main_module._default_llm_factory(cfg), OpenAICompatibleClient)


class TestTrustedHostsFromEnv:
    def test_unset_uses_loopback_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(main_module.TRUSTED_HOSTS_ENV, raising=False)
        assert main_module._trusted_hosts_from_env() == main_module.DEFAULT_TRUSTED_HOSTS

    def test_comma_list_is_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(main_module.TRUSTED_HOSTS_ENV, " a.test , ,b.test ")
        assert main_module._trusted_hosts_from_env() == ("a.test", "b.test")

    def test_only_separators_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(main_module.TRUSTED_HOSTS_ENV, " , , ")
        assert main_module._trusted_hosts_from_env() == main_module.DEFAULT_TRUSTED_HOSTS


class TestStaticFileWithin:
    def test_file_inside_dist(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        assert main_module._static_file_within(tmp_path, "a.txt") == (tmp_path / "a.txt").resolve()

    def test_traversal_is_rejected(self, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
        assert main_module._static_file_within(dist.resolve(), "../secret.txt") is None

    def test_null_byte_is_rejected(self, tmp_path: Path) -> None:
        assert main_module._static_file_within(tmp_path, "a\x00b") is None

    def test_directory_is_not_a_file(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        assert main_module._static_file_within(tmp_path, "sub") is None


class TestAccessGateWiring:
    """访问码必须真的挂在组装根上，而不是只在平台单测里成立。"""

    def test_remote_requests_are_gated_and_login_unlocks(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from hunter1.main import create_app

        class _Fetcher:
            def get_text(self, *_args: object, **_kwargs: object) -> str:
                raise AssertionError("测试不应发起抓取")

        context = main_module.AppContext.default(
            db_path=tmp_path / "h.db",
            fetcher=_Fetcher(),  # type: ignore[arg-type]
            access_token="test-code",
        )
        app = create_app(context)
        try:
            with TestClient(app, client=("192.168.1.20", 5000)) as client:
                denied = client.get("/api/jobs")
                assert denied.status_code == 401
                assert denied.headers["x-frame-options"] == "DENY"

                login = client.post("/__access", data={"code": "test-code"}, follow_redirects=False)
                assert login.status_code == 303
                assert client.get("/api/jobs").status_code == 200
        finally:
            context.db.dispose()

    def test_native_header_reaches_sync_endpoint_through_all_middleware(
        self, tmp_path: Path
    ) -> None:
        """安卓 App 的访问码头必须穿过 Host / 安全边界 / 访问码三层，直达同步端点。"""
        from fastapi.testclient import TestClient

        from hunter1.main import create_app

        class _Fetcher:
            def get_text(self, *_args: object, **_kwargs: object) -> str:
                raise AssertionError("测试不应发起抓取")

        context = main_module.AppContext.default(
            db_path=tmp_path / "h.db",
            fetcher=_Fetcher(),  # type: ignore[arg-type]
            access_token="test-code",
        )
        app = create_app(context)
        snapshot = {
            "version": 1,
            "exported_at": "2026-10-20T12:00:00Z",
            "jobs": [],
            "applications": [],
            "profile": None,
        }
        try:
            with TestClient(app, client=("192.168.1.20", 5000)) as client:
                denied = client.post("/api/sync/merge", json=snapshot)
                assert denied.status_code == 401

                allowed = client.post(
                    "/api/sync/merge",
                    json=snapshot,
                    headers={"X-Hunter1-Access-Code": "test-code"},
                )
                assert allowed.status_code == 200
                assert allowed.json()["snapshot"]["version"] == 1

                pulled = client.get(
                    "/api/sync/snapshot",
                    headers={"X-Hunter1-Access-Code": "test-code"},
                )
                assert pulled.status_code == 200
        finally:
            context.db.dispose()
