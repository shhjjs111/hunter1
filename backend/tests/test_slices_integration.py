"""组装处集成测试 —— 切片 API 在真实 app 里端到端可达。

与各切片自己的 router 测试的区别：那些测「切片作为独立单元」，
这里测**组装后的整体** —— 路由挂载、前缀、依赖装配、与旧 SSR 页面共存。

不 mock 中间层：真 SQLite、真路由表、真依赖装配；只有外部世界（模型、站点）
用替身。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hunter1.domain.crawl import RawJob
from hunter1.domain.llm import LLMResponse
from hunter1.domain.models import Job
from hunter1.main import AppContext, create_app
from hunter1.platform.db import Database
from hunter1.slices.scoring.models import CandidateProfile

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
JOB_ID = "i" * 32


class _FakeFetcher:
    def get_text(self, url: str, **_kw: object) -> str:
        return "<html></html>"


class _FakeCrawler:
    """一个能产出岗位的假抓取器（真链路：走 crawl 用例落库）。"""

    key = "fake_site"
    company = "假站点"
    careers_url = "https://fake.example.com/jobs"

    def fetch(self) -> list[RawJob]:
        return [RawJob(company="假公司", title="假岗位", detail_url="https://fake.example.com/j/1")]


class _FakeLLM:
    def complete_structured(self, **_kw: Any) -> LLMResponse:
        return LLMResponse(content='{"score": 66, "summary": "还行"}', model="fake")

    def complete_with_tools(self, **_kw: Any) -> LLMResponse:
        return LLMResponse(content="助手回复", model="fake")

    def stream_with_tools(self, **_kw: Any) -> Iterator[Any]:
        from hunter1.domain.llm import StreamComplete, TextDelta

        yield TextDelta("助手")
        yield TextDelta("回复")
        yield StreamComplete(content="助手回复", model="fake")


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[tuple[TestClient, Database]]:
    db = Database(tmp_path / "integration.db")
    db.initialize()
    db.jobs().upsert(
        Job(
            id=JOB_ID,
            company_id="c1",
            title="集成测试岗",
            detail_url="https://x/1",
            source="实习僧",
            company_name="集成公司",
            last_seen_at=NOW,
        )
    )
    context = AppContext(
        db=db,
        fetcher=_FakeFetcher(),
        llm_factory=lambda _settings: _FakeLLM(),  # type: ignore[arg-type,return-value]
        clock=lambda: NOW,
        candidate_profile=CandidateProfile(keywords=["集成"], summary="集成测试画像"),
    )
    # 让评分端点有可用的「模型配置」（否则 _slice_llm 会抛「模型未配置」）
    from hunter1.domain.settings import LLMSettings

    db.settings().save_llm(
        LLMSettings(base_url="https://api.example.com/v1", model="m", api_key="sk-x")
    )

    app = create_app(context)
    # 抓取 runner 的工厂指向假抓取器（真链路，只换站点）
    app.state.runner._crawler_factory = lambda: [_FakeCrawler()]  # type: ignore[attr-defined]
    with TestClient(app) as test_client:
        yield test_client, db


class TestSliceApisAreMounted:
    """五个切片的路由都要在组装后可达 —— 缺一个就是「接了一半」。"""

    def test_jobs_list(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _db = client
        response = test_client.get("/api/jobs")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["company"] == "集成公司"

    def test_job_detail_and_apply(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, db = client
        assert test_client.get(f"/api/jobs/{JOB_ID}").status_code == 200
        assert test_client.post(f"/api/jobs/{JOB_ID}/apply").status_code == 201
        assert db.applications().count() == 1

    def test_applications_list(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _db = client
        test_client.post(f"/api/jobs/{JOB_ID}/apply")
        payload = test_client.get("/api/applications").json()
        assert len(payload["items"]) == 1
        assert payload["items"][0]["company"] == "集成公司"

    def test_application_stage_flow(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _db = client
        application_id = test_client.post(f"/api/jobs/{JOB_ID}/apply").json()["application_id"]
        response = test_client.post(
            f"/api/applications/{application_id}/stage",
            json={"stage": "interview", "note": "一面"},
        )
        assert response.status_code == 200
        assert response.json()["stage"] == "interview"

    def test_scoring_endpoint(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, db = client
        response = test_client.post(f"/api/scoring/{JOB_ID}")
        assert response.status_code == 200
        assert response.json()["score"] == 66
        reloaded = db.jobs().get(JOB_ID)
        assert reloaded is not None and reloaded.match_score == 66

    def test_crawl_status(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _db = client
        payload = test_client.get("/api/crawl/status").json()
        assert payload["running"] is False

    def test_assistant_conversations(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _db = client
        assert test_client.get("/api/assistant/conversations").json() == []


class TestRealCrawlThroughApi:
    """抓取端点跑的是**真链路**：runner → crawl 用例 → SQLite。"""

    def test_crawl_lands_jobs_in_db(self, client) -> None:  # type: ignore[no-untyped-def]
        import time

        test_client, db = client
        assert test_client.post("/api/crawl").json()["started"] is True

        deadline = time.monotonic() + 10
        while (
            test_client.get("/api/crawl/status").json()["running"] and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        # 库里多了假站点抓到的那条岗位
        assert db.jobs().count() == 2
        payload = test_client.get("/api/jobs", params={"q": "假岗位"}).json()
        assert payload["total"] == 1


class TestAssistantStreamThroughApi:
    def test_sse_stream_completes_and_persists(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, db = client
        response = test_client.post("/api/assistant/stream", json={"message": "你好"})
        assert response.status_code == 200
        assert "助手回复" in response.text
        assert '"type": "done"' in response.text
        assert len(db.conversations().list()) == 1


def _seeded_job() -> Job:
    """集成测试用的种子岗位。"""
    return Job(
        id=JOB_ID,
        company_id="c1",
        title="集成测试岗",
        detail_url="https://x/1",
        source="实习僧",
        company_name="集成公司",
        last_seen_at=NOW,
    )


def _make_app(tmp_path: Path, *, frontend: Path | None) -> tuple[TestClient, Database]:
    """按指定前端目录装配一个 app（None = 不提供产物）。"""
    from hunter1.main import frontend_dir as _fd

    db = Database(tmp_path / f"app-{frontend is not None}.db")
    db.initialize()
    db.jobs().upsert(_seeded_job())
    context = AppContext(
        db=db,
        fetcher=_FakeFetcher(),
        llm_factory=lambda _settings: _FakeLLM(),  # type: ignore[arg-type,return-value]
        clock=lambda: NOW,
    )
    if frontend is not None:
        monkey_target = frontend
        import hunter1.main as main_module

        original = main_module.frontend_dir
        main_module.frontend_dir = lambda: monkey_target  # type: ignore[assignment]
        try:
            app = create_app(context)
        finally:
            main_module.frontend_dir = original  # type: ignore[assignment]
    else:
        import hunter1.main as main_module

        original = main_module.frontend_dir
        main_module.frontend_dir = lambda: None  # type: ignore[assignment]
        try:
            app = create_app(context)
        finally:
            main_module.frontend_dir = original  # type: ignore[assignment]
    assert _fd is not None
    return TestClient(app), db


class TestFrontendServing:
    """Wave 6：后端不再渲染页面，只服务前端构建产物。

    两个方向都要测（确定性由「显式指定产物目录」保证，不依赖本机是否 build 过）：
    - 没有产物 → 503 + 可行动的提示（不是 500、不是白屏）；
    - 有产物 → SPA 回落：任意前端路由返回 index.html，真实文件按文件返回。
    """

    def test_without_build_explains_actionably(self, tmp_path: Path) -> None:
        test_client, db = _make_app(tmp_path, frontend=None)
        response = test_client.get("/")
        assert response.status_code == 503
        payload = response.json()
        assert "前端产物未构建" in payload["detail"]
        assert "dev.sh" in payload["hint"]  # 给出可行动的下一步
        db.dispose()

    def test_with_build_serves_spa_and_falls_back(self, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html><div id=root></div></html>", encoding="utf-8")
        (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")

        test_client, db = _make_app(tmp_path, frontend=dist)

        # 根路径 → index.html
        root = test_client.get("/")
        assert root.status_code == 200 and "id=root" in root.text

        # 前端路由（/settings 等）→ 回落 index.html（SPA 自己解析路径）
        for path in ("/settings", "/applications", "/assistant", "/crawl"):
            fallback = test_client.get(path)
            assert fallback.status_code == 200, f"{path} 未回落"
            assert "id=root" in fallback.text

        # 真实文件优先
        assert test_client.get("/favicon.svg").status_code == 200
        assert test_client.get("/assets/app.js").status_code == 200
        db.dispose()

    def test_api_takes_precedence_over_spa_fallback(self, tmp_path: Path) -> None:
        """SPA 回落不能吞掉 API —— 否则前端永远拿不到数据。"""
        dist = tmp_path / "dist2"
        dist.mkdir()
        (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
        test_client, db = _make_app(tmp_path, frontend=dist)

        response = test_client.get("/api/jobs")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["total"] == 1
        db.dispose()

    def test_no_legacy_ssr_layer(self, tmp_path: Path) -> None:
        """旧 SSR 层已删 —— 渲染层只有前端一处。"""
        import hunter1

        package_dir = Path(hunter1.__file__).resolve().parent
        assert not (package_dir / "web").exists()
