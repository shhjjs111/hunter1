"""Web 路由测试 —— 假模型 + 假抓取器 + 真 SQLite，全程离线。

覆盖三件事：
1. 每个页面都能打开并渲染出内容（不是空壳）；
2. 写操作真的改了库（记录投递、改阶段、删记录、存配置）；
3. **失败路径有反馈**：配置不全、模型报错时页面要说清楚，不能没反应。
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from hunter1.domain.models import ApplicationStage
from hunter1.infrastructure.db import Database
from hunter1.web.app import create_app
from hunter1.web.context import AppContext
from tests.web.helpers import NOW, FakeFetcher, FakeLLM, configure_llm

# 模型替身与 app_env fixture 已移到 tests/web/helpers.py 与 conftest.py ——
# 流式与非流式两条路径必须共用同一个 FakeLLM，两份定义迟早会漂移。
_configure = configure_llm


class TestJobsPage:
    def test_index_lists_jobs(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        response = client.get("/")
        assert response.status_code == 200
        assert "AI产品经理" in response.text
        assert "字节跳动" in response.text  # 公司名（不是哈希）
        assert "行政专员" in response.text

    def test_search_filters(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        response = client.get("/", params={"q": "产品经理"})
        assert "AI产品经理" in response.text
        assert "行政专员" not in response.text

    def test_search_no_match_shows_hint(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        response = client.get("/", params={"q": "律师"})
        assert response.status_code == 200
        assert "共 0 条" in response.text

    def test_empty_library_shows_hint(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "empty.db")
        db.initialize()
        context = AppContext(
            db=db, fetcher=FakeFetcher("<html></html>"), llm_factory=lambda s: FakeLLM()
        )
        with TestClient(create_app(context)) as client:
            response = client.get("/")
        assert "岗位库还是空的" in response.text

    def test_page_param_is_clamped(self, app_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """page 无上界时 page=999999 会变成天量 OFFSET —— 必须压到上限。

        超界时页面渲染为空列表（页码不可见），所以直接观察下推到仓储的
        offset：它就是「SQLite 要扫描并丢弃的行数」。
        """
        client, _db, _llm = app_env
        from hunter1.infrastructure.db.repository import SqliteJobRepository

        seen: list[int] = []
        original = SqliteJobRepository.list

        def spy(self, *, limit: int = 100, offset: int = 0):  # type: ignore[no-untyped-def]
            seen.append(offset)
            return original(self, limit=limit, offset=offset)

        monkeypatch.setattr(SqliteJobRepository, "list", spy)
        response = client.get("/", params={"page": "999999"})
        assert response.status_code == 200
        # 钳到第 10000 页：offset = 9999 * 20（默认 page_size）
        assert seen == [9_999 * 20]


class TestApplyFlow:
    def test_record_application_from_job(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        response = client.post("/jobs/" + "j1" + "0" * 30 + "/apply", follow_redirects=True)
        assert response.status_code == 200
        assert "已记录该投递" in response.text
        assert "字节跳动" in response.text
        assert db.applications().count() == 1

    def test_unknown_job_is_404(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        assert client.post("/jobs/nope/apply").status_code == 404


class TestApplicationsPage:
    def _record(self, db: Database) -> str:
        from hunter1.application.applications import new_application

        job = db.jobs().get("j1" + "0" * 30)
        assert job is not None
        application = new_application(job=job, now=NOW, application_id="a1")
        db.applications().upsert(application)
        return application.id

    def test_empty_shows_hint(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        assert "还没有投递记录" in client.get("/applications").text

    def test_lists_records(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        self._record(db)
        response = client.get("/applications")
        assert "字节跳动" in response.text
        assert "已投递" in response.text

    def test_change_stage(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        self._record(db)
        client.post("/applications/a1/stage", data={"stage": "interview", "note": "二面"})
        loaded = db.applications().get("a1")
        assert loaded is not None
        assert loaded.stage is ApplicationStage.INTERVIEW
        assert loaded.note == "二面"

    def test_changing_stage_keeps_note_when_blank(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        self._record(db)
        client.post("/applications/a1/stage", data={"stage": "interview", "note": "二面"})
        client.post("/applications/a1/stage", data={"stage": "offer", "note": ""})
        loaded = db.applications().get("a1")
        assert loaded is not None
        assert loaded.stage is ApplicationStage.OFFER
        assert loaded.note == "二面"

    def test_unknown_stage_is_400(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        self._record(db)
        response = client.post("/applications/a1/stage", data={"stage": "nonsense"})
        assert response.status_code == 400

    def test_unknown_application_is_404(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        assert client.post("/applications/nope/stage", data={"stage": "offer"}).status_code == 404

    def test_delete(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        self._record(db)
        client.post("/applications/a1/delete")
        assert db.applications().count() == 0


class TestSettingsPage:
    def test_renders_form_with_presets(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        response = client.get("/settings")
        assert response.status_code == 200
        assert "Base URL" in response.text
        assert "deepseek" in response.text  # 厂商预设

    def test_save_persists(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        client.post(
            "/settings",
            data={
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "api_key": "sk-abc",
                "temperature": "0.3",
                "max_tokens": "900",
            },
        )
        saved = db.settings().get_llm()
        assert saved is not None
        assert saved.model == "deepseek-chat"
        assert saved.temperature == 0.3
        assert saved.max_tokens == 900

    def test_blank_api_key_keeps_existing(self, app_env) -> None:  # type: ignore[no-untyped-def]
        """界面只回显掩码，所以空 key 必须理解为「不改」，不能把 key 抹掉。"""
        client, db, _llm = app_env
        _configure(db)
        client.post(
            "/settings",
            data={"base_url": "https://api.example.com/v1", "model": "m2", "api_key": ""},
        )
        saved = db.settings().get_llm()
        assert saved is not None
        assert saved.api_key == "sk-test"
        assert saved.model == "m2"

    def test_new_key_overrides(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        _configure(db)
        client.post(
            "/settings",
            data={"base_url": "https://api.example.com/v1", "model": "m", "api_key": "sk-new"},
        )
        saved = db.settings().get_llm()
        assert saved is not None and saved.api_key == "sk-new"

    def test_masked_key_is_not_leaked_in_page(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        _configure(db)
        response = client.get("/settings")
        assert "sk-test" not in response.text  # 完整 key 不能出现在页面里

    def test_api_key_input_is_masked(self, app_env) -> None:  # type: ignore[no-untyped-def]
        """密钥输入框必须是 password —— type=text 会让肩窥直接读到。

        这与 README 承诺的「界面只回显掩码、不输出完整密钥」是同一条要求：
        回显掩码但输入框明文，等于把承诺的一半丢掉了。
        """
        client, _db, _llm = app_env
        body = client.get("/settings").text
        assert 'id="api_key" type="password"' in body
        assert 'id="api_key" type="text"' not in body

    def test_invalid_base_url_is_reported_inline(self, app_env) -> None:  # type: ignore[no-untyped-def]
        """填错地址要就地告诉他哪里错，并把刚填的内容留着 —— 不是甩一个 500。"""
        client, db, _llm = app_env
        response = client.post(
            "/settings",
            data={"base_url": "not-a-url", "model": "m", "api_key": "sk-x"},
        )
        assert response.status_code == 200
        assert "必须是 http(s) 地址" in response.text
        assert 'value="not-a-url"' in response.text  # 用户输入被保留
        assert db.settings().get_llm() is None  # 没有落库

    def test_save_and_probe_roundtrip(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        client.post(
            "/settings",
            data={
                "base_url": "https://api.example.com/v1",
                "model": "m",
                "api_key": "sk-test",
            },
            follow_redirects=True,
        )
        assert "已保存" in client.get("/settings", params={"saved": "1"}).text
        assert db.settings().get_llm() is not None

    def test_connection_probe_success(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        _configure(db)
        response = client.post("/settings/test")
        assert "连接成功" in response.text

    def test_connection_probe_failure_is_shown(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        _configure(db)
        llm.reply = None
        response = client.post("/settings/test")
        assert "连接失败" in response.text
        assert "模型不可用" in response.text

    def test_probe_without_config_explains(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        assert "配置不完整" in client.post("/settings/test").text


class TestAssistantPage:
    def test_renders_when_unconfigured(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        response = client.get("/assistant")
        assert response.status_code == 200
        assert "还没配好模型" in response.text

    def test_send_without_config_redirects_with_error(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        response = client.post("/assistant", data={"message": "你好"}, follow_redirects=True)
        assert "请先在" in response.text
        assert db.conversations().list() == []  # 没配好就不要建空会话

    def test_conversation_roundtrip(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        _configure(db)
        response = client.post(
            "/assistant", data={"message": "有哪些岗位？"}, follow_redirects=True
        )
        assert response.status_code == 200
        assert "有哪些岗位？" in response.text  # 用户消息
        assert llm.reply is not None and llm.reply in response.text  # 助手回复

        conversations = db.conversations().list()
        assert len(conversations) == 1
        messages = db.conversations().messages(conversations[0].id)
        assert [message.role.value for message in messages] == ["user", "assistant"]

    def test_second_message_reuses_conversation(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        _configure(db)
        client.post("/assistant", data={"message": "第一问"}, follow_redirects=True)
        conversations = db.conversations().list()
        assert len(conversations) == 1
        client.post(
            "/assistant",
            data={"message": "第二问", "conversation_id": conversations[0].id},
            follow_redirects=True,
        )
        assert len(db.conversations().list()) == 1
        assert len(db.conversations().messages(conversations[0].id)) == 4

    def test_model_failure_is_surfaced_and_no_conversation_created(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, llm = app_env
        _configure(db)
        llm.reply = None
        response = client.post("/assistant", data={"message": "你好"}, follow_redirects=True)
        assert "模型不可用" in response.text
        # 失败不留空会话：会话列表里不该多出一个「你好」的空壳
        assert db.conversations().list() == []

    def test_blank_message_is_ignored(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        _configure(db)
        client.post("/assistant", data={"message": "   "}, follow_redirects=True)
        assert db.conversations().list() == []


class TestCrawlPage:
    def test_renders_before_any_run(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        response = client.get("/crawl")
        assert response.status_code == 200
        assert "开始抓取" in response.text

    def test_start_crawls_registered_sites_into_db(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, db, _llm = app_env
        before = db.jobs().count()
        response = client.post("/crawl", follow_redirects=True)
        assert response.status_code == 200
        assert "已开始抓取" in response.text

        deadline = time.monotonic() + 10
        while db.jobs().count() == before and time.monotonic() < deadline:
            time.sleep(0.05)
        assert db.jobs().count() > before  # 快照里的岗位确实入库了

        page = client.get("/crawl")
        assert "高校人才网" in page.text
        assert "完成" in page.text

    def test_status_endpoint_is_json(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        payload = client.get("/api/crawl/status").json()
        assert payload["running"] is False
        assert "sites" in payload

    def test_second_start_while_running_is_refused(self, app_env) -> None:  # type: ignore[no-untyped-def]
        client, _db, _llm = app_env
        app = client.app
        # 直接占住 runner，模拟「上一轮还没跑完」
        assert app.state.runner.start() is True
        response = client.post("/crawl", follow_redirects=True)
        assert "上一轮还在跑" in response.text
