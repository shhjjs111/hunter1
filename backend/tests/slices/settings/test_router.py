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
from hunter1.platform.db.settings import LLM_KEY
from hunter1.slices.settings.router import build_router
from hunter1.slices.settings.schemas import (
    MAX_API_KEY_CHARS,
    MAX_BASE_URL_CHARS,
    MAX_MODEL_CHARS,
)
from hunter1.slices.settings.store import SettingsStore


class FakeLLM:
    def close(self) -> None:
        """端口要求：释放底层资源；内存假件是 no-op。"""

    def __init__(self, *, boom: bool = False) -> None:
        self.boom = boom

    def complete(self, **_kw: Any) -> LLMResponse:
        if self.boom:
            raise RuntimeError("endpoint unreachable")
        return LLMResponse(content="可用", model="fake-model")


def _boom_factory(_settings: LLMSettings) -> Any:
    """工厂自己抛错 —— 客户端库可能因 URL / 代理配置在构造期就失败。"""
    raise RuntimeError("factory exploded")


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
        """校验失败就地给原因，不是 500、不是静默丢弃。

        detail 的形状与 FastAPI 的请求校验 422 **一致**（`[{loc, msg, type}]`）：
        同一个端点上两种 422 体，前端就得写两套解析。文案本身仍要人读得懂。
        """
        for client in _client(db):
            response = client.put("/api/settings", json={**FORM, "base_url": "not-a-url"})
            assert response.status_code == 422
            detail = response.json()["detail"]
            assert isinstance(detail, list), (
                f"422 的 detail 应是 FastAPI 同款数组，实际 {type(detail)}"
            )
            first = detail[0]
            assert set(first) >= {"loc", "msg", "type"}
            assert first["loc"][0] == "body"
            assert "http(s)" in first["msg"]
            assert db.settings().get_llm() is None  # 没有落库

    def test_overlong_fields_are_422_and_not_stored(self, db: Database) -> None:
        """三个字段都有长度上限 —— 它们既落进配置库，又**原样**带去请求上游。

        没有上限时，一次手滑粘贴（把整份密钥/配置文件贴进输入框）就能把巨量文本存进
        配置，而且之后每次请求都带着它。这里用**超过上限一个字符**证明边界真的落在
        阈值上（不是随手填个大数），并且断言一个都没落库。
        """
        over_limit = {
            "base_url": "https://" + "a" * MAX_BASE_URL_CHARS,
            "model": "m" * (MAX_MODEL_CHARS + 1),
            "api_key": "k" * (MAX_API_KEY_CHARS + 1),
        }
        for client in _client(db):
            for field, value in over_limit.items():
                response = client.put("/api/settings", json={**FORM, field: value})
                assert response.status_code == 422, f"{field} 超长没有被拒"
                assert response.json()["detail"][0]["loc"][-1] == field
            assert db.settings().get_llm() is None  # 一个都没落库


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

    def test_error_message_never_carries_the_full_key(self, db: Database) -> None:
        """探测失败原因里不许出现完整 API Key。

        底层 HTTP 客户端的异常会带上请求细节，有的厂商把 Authorization 回显在
        4xx 体里；这段文案会显示在配置页上、也可能被用户贴给别人看。
        """
        stored = SettingsStore(db)
        client_app = FastAPI()
        captured: dict[str, str] = {}

        class EchoingLLM:
            def close(self) -> None:
                """端口要求：释放资源。"""

            def complete(self, **_kw: Any) -> LLMResponse:
                raise RuntimeError(f"401 unauthorized (api_key={captured['key']})")

        def factory(settings: LLMSettings) -> EchoingLLM:
            captured["key"] = settings.api_key
            return EchoingLLM()

        client_app.include_router(
            build_router(store=stored, llm_factory=factory),
            prefix="/api",  # type: ignore[arg-type]
        )
        with TestClient(client_app) as client:
            client.put("/api/settings", json=FORM)
            settings = stored.get_llm()
            assert settings is not None and settings.api_key, "配置必须真的存进去了"
            payload = client.post("/api/settings/test").json()
        assert payload["ok"] is False
        assert settings.api_key not in payload["message"], "完整 key 被回显了"
        assert "***" in payload["message"], "该抹成掩码，而不是整句丢掉"

    def test_factory_failure_is_reported_not_raised(self, db: Database) -> None:
        """工厂自己抛错时也要给可读原因（且不能因为关闭对象未创建而盖掉原因）。"""
        app = FastAPI()
        app.include_router(
            build_router(
                store=SettingsStore(db),
                llm_factory=_boom_factory,  # type: ignore[arg-type]
            ),
            prefix="/api",
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            client.put("/api/settings", json=FORM)
            response = client.post("/api/settings/test")
        assert response.status_code == 200, "工厂炸了不该是 500（配置页会丢掉全部指引）"
        payload = response.json()
        assert payload["ok"] is False
        assert "factory exploded" in payload["message"]


def test_api_key_input_must_not_leak_through_settings_view(db: Database) -> None:
    """掩码函数的形态与其他测试一致：前缀 + 末四位。"""
    settings = LLMSettings(**FORM)
    assert settings.masked_key() == "sk-…1234"


class TestCorruptedConfigIsRepairable:
    """配置数据损坏时，配置页必须能打开、能修 —— 不能 500 自锁。

    `platform/db/settings.py` 的 `get_llm()` 在数据损坏时抛错是**对的**设计
    （区分「没配过」与「配过但坏了」）。但 settings 路由不接这个错，后果是
    用户唯一的重填路径（在配置页覆盖保存）被自己的 500 堵死：

        GET /settings  → 500
        PUT /settings  → 500（为「空 key = 保留原值」也要先读 existing）

    scoring 切片对同一问题已按「GET 给 200 + warning、评分给 409」处理过
    （理由写在那里：不能 500，那会让用户连配置页都打不开）。这里补上同一课。
    """

    def _corrupt(self, db: Database) -> None:
        """往配置键里塞一个 LLMSettings 校验不过的值（模拟数据损坏/旧版本遗留）。"""
        db.settings().set_raw(LLM_KEY, {"base_url": 123, "model": None})

    def test_get_returns_200_not_500(self, db: Database) -> None:
        self._corrupt(db)
        for client in _client(db):
            response = client.get("/api/settings")
        assert response.status_code == 200, f"配置页打不开就没法修，实际 {response.status_code}"

    def test_get_says_it_is_broken_not_unconfigured(self, db: Database) -> None:
        """必须与「从没配过」区分 —— 否则用户填过的内容无声消失。"""
        self._corrupt(db)
        for client in _client(db):
            body = client.get("/api/settings").json()
        assert body is not None and body.get("broken"), f"要明确告知配置已损坏，实际 {body}"

    def test_put_can_repair(self, db: Database) -> None:
        """关键：用户能靠重填把自己救出来。"""
        self._corrupt(db)
        for client in _client(db):
            response = client.put("/api/settings", json=FORM)
            assert response.status_code == 200, f"修复路径被堵死，实际 {response.status_code}"
            # 修好后 GET 应回到正常视图
            assert client.get("/api/settings").json()["model"] == "m"

    def test_put_with_empty_key_on_broken_config_still_works(self, db: Database) -> None:
        """破损数据里没有可保留的 key —— 空 key 不能再要求「先读出原值」。"""
        self._corrupt(db)
        for client in _client(db):
            response = client.put("/api/settings", json={**FORM, "api_key": ""})
        assert response.status_code == 200

    def test_probe_reports_corruption_instead_of_500(self, db: Database) -> None:
        """探测端点也必须接住损坏 —— 它是配置页上的按钮，不能以 500 告终。

        SLICE.md 把 `/settings/test` 与评分/助手并列，要求损坏时「不是 500」。
        GET/PUT 都接住了，唯独探测此前裸调 `store.get_llm()`：损坏配置下
        `ValueError` 直接穿透成 500，前端 `useTestConnection` 只显示一句
        「探测失败」，把「重填即可自救」这个唯一出路埋掉。
        """
        self._corrupt(db)
        for client in _client(db):
            response = client.post("/api/settings/test")
        assert response.status_code == 200, f"探测不该 500，实际 {response.status_code}"
        body = response.json()
        assert body["ok"] is False
        assert "不可用" in body["message"]

    @staticmethod
    def _corrupt_to_non_object(db: Database) -> None:
        """把配置值写成**非对象**（手工改库 / 外部工具才可能出现的形状）。

        与 `_corrupt` 的区别：那里是一个字段类型不对的**对象**（走 `get_llm` 的
        `ValidationError→ValueError` 分支）；这里连对象都不是 —— `dict(123)` 曾抛
        `TypeError`，绕过全仓统一的 `except ValueError` 守卫直穿成 500。
        """
        import json

        from sqlalchemy import text

        with db.engine.begin() as conn:
            conn.execute(
                text("INSERT OR REPLACE INTO settings (key, value) VALUES (:k, :v)"),
                {"k": LLM_KEY, "v": json.dumps(123)},
            )

    def test_get_with_non_object_config_is_not_500(self, db: Database) -> None:
        """配置页必须能打开去修 —— 非对象值也不能把自己锁在门外。"""
        self._corrupt_to_non_object(db)
        for client in _client(db):
            response = client.get("/api/settings")
        assert response.status_code == 200, f"配置页打不开就没法修，实际 {response.status_code}"
        assert response.json()["broken"] is True

    def test_probe_with_non_object_config_is_not_500(self, db: Database) -> None:
        self._corrupt_to_non_object(db)
        for client in _client(db):
            response = client.post("/api/settings/test")
        assert response.status_code == 200, f"非对象配置也要给可读结论，实际 {response.status_code}"
        assert response.json()["ok"] is False


class TestPlaintextWarningIsExposed:
    """明文公网 base_url 的提示必须一路走到响应里（后端算、前端显示）。

    判断逻辑只有一处（`domain.settings.plaintext_warning`），路由负责把它放进
    `warning` 字段 —— 前端读 `settings.data.warning` 显示，不再自己判一遍。
    """

    def test_public_http_url_is_flagged(self, db: Database) -> None:
        form = {**FORM, "base_url": "http://api.example.com/v1"}
        for client in _client(db):
            response = client.put("/api/settings", json=form)
            assert response.status_code == 200
            assert response.json()["warning"], "明文公网端点必须给出提示"
            assert "http://" in response.json()["warning"]

    def test_https_url_has_no_warning(self, db: Database) -> None:
        for client in _client(db):
            assert client.put("/api/settings", json=FORM).json()["warning"] is None

    def test_local_http_url_has_no_warning(self, db: Database) -> None:
        """本机/内网用 http 是合理的（ollama 等）—— 不该被反复念叨。"""
        form = {**FORM, "base_url": "http://127.0.0.1:11434/v1"}
        for client in _client(db):
            assert client.put("/api/settings", json=form).json()["warning"] is None
