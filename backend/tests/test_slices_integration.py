"""组装处集成测试 —— 切片 API 在真实 app 里端到端可达。

与各切片自己的 router 测试的区别：那些测「切片作为独立单元」，
这里测**组装后的整体** —— 路由挂载、前缀、依赖装配、与旧 SSR 页面共存。

不 mock 中间层：真 SQLite、真路由表、真依赖装配；只有外部世界（模型、站点）
用替身。
"""

from __future__ import annotations

import asyncio
import json
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
    def close(self) -> None:
        """端口要求：释放底层资源；内存假件是 no-op。"""

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
    )
    # 让评分端点有可用的「模型配置」与「候选人画像」
    # （否则 _slice_llm 抛「模型未配置」、scoring 报 409 画像未配置）
    from hunter1.domain.settings import LLMSettings

    db.settings().save_llm(
        LLMSettings(base_url="https://api.example.com/v1", model="m", api_key="sk-x")
    )
    from hunter1.slices.scoring import CandidateProfile
    from hunter1.slices.scoring.store import ScoreStore

    ScoreStore(db).save_profile(CandidateProfile(keywords=["集成"], summary="集成测试画像"))

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
        # 投递入口归 applications（投递记录本体在那里）—— body 带 job_id
        applied = test_client.post("/api/applications", json={"job_id": JOB_ID})
        assert applied.status_code == 201
        assert db.applications().count() == 1

    def test_applications_list(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _db = client
        test_client.post("/api/applications", json={"job_id": JOB_ID})
        payload = test_client.get("/api/applications").json()
        assert len(payload["items"]) == 1
        assert payload["items"][0]["company"] == "集成公司"

    def test_application_stage_flow(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _db = client
        application_id = test_client.post("/api/applications", json={"job_id": JOB_ID}).json()[
            "application_id"
        ]
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
        payload = test_client.get("/api/assistant/conversations").json()
        assert payload["items"] == []
        assert payload["total"] == 0 and payload["has_next"] is False


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
    db = Database(tmp_path / f"app-{frontend is not None}.db")
    db.initialize()
    db.jobs().upsert(_seeded_job())
    context = AppContext(
        db=db,
        fetcher=_FakeFetcher(),
        llm_factory=lambda _settings: _FakeLLM(),  # type: ignore[arg-type,return-value]
        clock=lambda: NOW,
    )
    # 产物目录由**显式指定**决定，不依赖本机是否 build 过（否则测试结果随开发机变化）。
    # 直接把 frontend_dir 换成返回值（None 也算），两个方向走同一条路径。
    import hunter1.main as main_module

    original = main_module.frontend_dir
    main_module.frontend_dir = lambda: frontend  # type: ignore[assignment]
    try:
        app = create_app(context)
    finally:
        main_module.frontend_dir = original  # type: ignore[assignment]
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

    def test_half_built_dist_explains_instead_of_crashing(self, tmp_path: Path) -> None:
        """产物目录在、`index.html` 不在 → 503 + 提示，**不是** 500。

        回归护栏：`frontend_dir()` 原先只判目录存在，于是这条路径上 SPA 回落的
        `FileResponse` 抛 `RuntimeError: File at path ... does not exist` —— 用户拿到
        500，而真正的原因（产物不完整）一个字都没说。半截构建、`HUNTER1_FRONTEND_DIR`
        指错、打包产物不完整都会落到这里。
        """
        half = tmp_path / "half-built"
        (half / "assets").mkdir(parents=True)
        (half / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

        test_client, db = _make_app(tmp_path, frontend=half)

        for path in ("/", "/settings"):
            response = test_client.get(path)
            assert response.status_code == 503, f"{path} 应给可读 503，实得 {response.status_code}"
            assert "前端产物未构建" in response.json()["detail"]
        db.dispose()

    def test_products_removed_after_startup_do_not_crash(self, tmp_path: Path) -> None:
        """启动后产物被换掉（部署脚本正在覆盖目录）→ 可读 503，不是 500。

        这条钉的是 `spa()` 里的第二道闸：只靠 `frontend_dir()` 在启动时判一次是不够的，
        目录会在进程活着的时候被换 —— 那时 `FileResponse` 又会抛 `RuntimeError`。
        """
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        index = dist / "index.html"
        index.write_text("<html><div id=root></div></html>", encoding="utf-8")

        test_client, db = _make_app(tmp_path, frontend=dist)
        assert test_client.get("/").status_code == 200

        index.unlink()  # 部署脚本换目录的那一瞬

        response = test_client.get("/")
        assert response.status_code == 503, f"应给可读 503，实得 {response.status_code}"
        assert "前端产物未构建" in response.json()["detail"]
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

    def test_unregistered_api_path_is_json_404_not_spa(self, tmp_path: Path) -> None:
        """**未注册**的 `/api/*` 不能回落到 index.html（200 + HTML）。

        「后注册」只保护**已注册**的路径：拼错端点的 GET 会拿到 HTML，前端
        `response.json()` 直接炸，而且看不出是路径写错了。`/api/*` 的存在性
        必须由路由表回答，不能由 SPA 兜底。
        """
        dist = tmp_path / "dist_api"
        dist.mkdir()
        (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
        test_client, db = _make_app(tmp_path, frontend=dist)

        response = test_client.get("/api/does-not-exist")
        assert response.status_code == 404, "未注册的 API 路径被 SPA 兜底吞掉了"
        assert response.headers["content-type"].startswith("application/json")
        assert "spa" not in response.text
        db.dispose()

    def test_wrong_method_on_api_route_is_405_not_spa(self, tmp_path: Path) -> None:
        """已注册路径、方法不对 → 405 + Allow（而不是 200 + HTML）。"""
        dist = tmp_path / "dist_method"
        dist.mkdir()
        (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
        test_client, db = _make_app(tmp_path, frontend=dist)

        response = test_client.get("/api/settings/test")  # 该端点只有 POST
        assert response.status_code == 405
        assert "POST" in response.headers.get("allow", "")
        db.dispose()

    def test_no_legacy_ssr_layer(self, tmp_path: Path) -> None:
        """旧 SSR 层已删 —— 渲染层只有前端一处。"""
        import hunter1

        package_dir = Path(hunter1.__file__).resolve().parent
        assert not (package_dir / "web").exists()


def _raw_asgi_get(app: Any, raw_path: str) -> tuple[int | None, bytes]:
    """以**原始 ASGI scope** 直接调用应用，绕开 TestClient/httpx 的 URL 规范化。

    攻击者（原始 socket、`curl --path-as-is`、浏览器发百分号编码 `%2e%2e%2f`）
    能让 `..` 原样抵达服务端；httpx/TestClient 会在客户端把路径规范化掉，用它
    测不出这条缺陷。「服务端是否守住边界」必须在 ASGI 层验证。
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8000")],
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 12345),
    }
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    status = next((m["status"] for m in messages if m["type"] == "http.response.start"), None)
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body


class TestSpaFallbackSecurity:
    """SPA 回落是**手工**拼路径（`dist / path`）—— 不做边界校验就能穿越到 dist 之外。

    `/assets` 走 StaticFiles，Starlette 内部有边界保护；这条手工路径没有。
    修复：候选路径解析后必须落在 dist 之内（`is_relative_to`），否则回落 index。
    """

    def test_traversal_does_not_escape_dist(self, tmp_path: Path) -> None:
        # 布局镜像真实仓库：<base>/frontend/dist 与 <base>/SECRET.txt（dist 上溯两级即根）
        dist = tmp_path / "frontend" / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html>SPA-ROOT</html>", encoding="utf-8")
        (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        (tmp_path / "SECRET.txt").write_text("TOP-SECRET-CONTENT", encoding="utf-8")

        test_client, db = _make_app(tmp_path, frontend=dist)

        # 恶意客户端构造的穿越路径，原样抵达 ASGI 层
        status, body = _raw_asgi_get(test_client.app, "/../../SECRET.txt")
        assert status == 200
        assert b"TOP-SECRET" not in body, "路径穿越：读到了 dist 之外的文件"
        assert b"SPA-ROOT" in body  # 越界请求回落 index，而不是泄露文件

        # 修复不能把正常服务一起关掉：dist 内的真实文件仍照常返回
        ok_status, ok_body = _raw_asgi_get(test_client.app, "/favicon.svg")
        assert ok_status == 200 and b"<svg/>" in ok_body
        db.dispose()

    def test_embedded_null_byte_path_is_not_a_500(self, tmp_path: Path) -> None:
        """`/a%00b` 这类路径不能让 `Path.resolve()` 的 ValueError 逃出去。

        未处理时 `os.stat` 抛 `ValueError: embedded null character in path`，
        从应用层逃逸 —— 线上就是一次 500。这类路径不可能对应真实文件，
        直接当作「没有这个文件」回落 index 即可。
        """
        dist = tmp_path / "frontend" / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html>SPA-ROOT</html>", encoding="utf-8")

        test_client, db = _make_app(tmp_path, frontend=dist)
        status, body = _raw_asgi_get(test_client.app, "/a\x00b")
        assert status == 200, f"含 NUL 的路径不该 5xx（实际 {status}）"
        assert b"SPA-ROOT" in body
        db.dispose()


def _production_context(tmp_path: Path, name: str) -> Any:
    """按 **cli.py 的生产装配方式** 构造 app：只给 db_path 与 site_keys。

    照抄 `cli.py` 的 `AppContext.default(db_path=..., site_keys=...)` —— 不注入
    任何替身。凡是"只有测试里才成立"的能力，这个装配会如实暴露。
    """
    from hunter1.main import AppContext, create_app

    context = AppContext.default(db_path=tmp_path / name, site_keys=[])
    return create_app(context)


class TestScoringReachableInProduction:
    """评分必须在**生产装配**下可达 —— 这是契约与运行时的一致性判据。

    `cli.py` 不注入候选人画像。若 scoring 路由只在「画像非 None」时挂载，
    成品里 `/api/scoring/*` 根本不存在，而 `contracts/openapi.json` 却声称它有
    （导出时传了占位画像）：前端照契约写代码会拿到 404。
    「契约声明了的能力必须在运行时真的可用」—— 否则契约不是契约，是愿望。
    """

    def test_scoring_routes_exist_without_injected_profile(self, tmp_path: Path) -> None:
        app = _production_context(tmp_path, "prod-reach.db")
        paths = set(app.openapi()["paths"])
        assert "/api/scoring/{job_id}" in paths, "生产装配下评分端点缺失 —— 前端会 404"
        assert "/api/scoring/profile" in paths, "画像读写端点缺失 —— 用户无从配置"
        app.state.context.db.dispose()

    def test_scoring_without_profile_is_actionable(self, tmp_path: Path) -> None:
        """未配画像 → 可操作的错误，而不是 404（端点不存在）或 500（崩了）。"""
        app = _production_context(tmp_path, "prod-noprofile.db")
        app.state.context.db.jobs().upsert(_seeded_job())

        with TestClient(app) as test_client:
            response = test_client.post(f"/api/scoring/{JOB_ID}")

        assert response.status_code == 409, f"期望 409（状态未就绪），实际 {response.status_code}"
        detail = response.json()["detail"]
        assert "画像" in detail, f"错误信息要指出缺什么，实际：{detail}"
        app.state.context.db.dispose()

    def test_missing_model_config_is_actionable_not_500(self, tmp_path: Path) -> None:
        """画像配了、模型没配 → 也是 409 + 指引，不是 500。

        「500 Internal Server Error」对用户毫无信息量，而真实原因是**初始状态**
        （还没填 API Key），不是服务端 bug。项目自己的原则是「不静默失败、
        模型报错要原样显示给用户」—— 500 正是这条原则的反面。
        """
        from hunter1.slices.scoring import CandidateProfile
        from hunter1.slices.scoring.store import ScoreStore

        app = _production_context(tmp_path, "prod-nomodel.db")
        app.state.context.db.jobs().upsert(_seeded_job())
        ScoreStore(app.state.context.db).save_profile(CandidateProfile(keywords=["集成"]))
        # 刻意不保存 LLM 配置

        with TestClient(app) as test_client:
            response = test_client.post(f"/api/scoring/{JOB_ID}")

        assert response.status_code == 409, (
            f"期望 409，实际 {response.status_code}：{response.text[:120]}"
        )
        detail = response.json()["detail"]
        assert "模型" in detail and "配置" in detail, f"要指出缺什么并给指引，实际：{detail}"
        app.state.context.db.dispose()

    def test_missing_model_config_covers_assistant_too(self, tmp_path: Path) -> None:
        """助手共用同一个 `_runtime_llm` —— 应用级处理器应当一并覆盖。

        这条断言是在验证「注册在应用级」这个**设计主张**本身：若将来有人把它
        挪回逐路由捕获，助手会重新变成 500，而这条测试会红。
        """
        app = _production_context(tmp_path, "prod-nomodel-assistant.db")

        with TestClient(app) as test_client:
            response = test_client.post("/api/assistant/turn", json={"message": "你好"})

        assert response.status_code == 409, (
            f"期望 409，实际 {response.status_code}：{response.text[:120]}"
        )
        assert "配置" in response.json()["detail"]
        app.state.context.db.dispose()

    def test_corrupted_model_config_is_409_not_500(self, tmp_path: Path) -> None:
        """配置**损坏**时同样给 409 + 指引，不是 500。

        `get_llm()` 对损坏数据抛 `ValueError`（这是对的：区分「没配」与「配坏了」）。
        若 `_runtime_llm` 不接，它会绕过 `ModelNotConfiguredError` 的处理器直穿成
        500 —— 而「去配置页重填」恰好是唯一出路，错误信息必须指向那里。
        """
        from hunter1.platform.db.settings import LLM_KEY
        from hunter1.slices.scoring import CandidateProfile
        from hunter1.slices.scoring.store import ScoreStore

        app = _production_context(tmp_path, "prod-brokenmodel.db")
        app.state.context.db.jobs().upsert(_seeded_job())
        # 必须**先配好画像**：否则 409 来自「画像未配置」，请求根本走不到读模型配置，
        # 测试会因断言里恰好含「配置」二字而假绿（这版就是踩了这个坑）。
        ScoreStore(app.state.context.db).save_profile(CandidateProfile(keywords=["集成"]))
        app.state.context.db.settings().set_raw(LLM_KEY, {"base_url": 123, "model": None})

        with TestClient(app) as test_client:
            response = test_client.post(f"/api/scoring/{JOB_ID}")

        assert response.status_code == 409, f"期望 409，实际 {response.status_code}"
        detail = response.json()["detail"]
        # 收紧断言：必须说的是**模型配置**损坏，不是「画像未配置」蒙混过关
        assert "模型" in detail and "画像" not in detail, f"要指出是模型配置问题，实际：{detail}"
        app.state.context.db.dispose()


class TestStructuredDegradationIsRememberedAcrossRequests:
    """降级发现只付一次代价 —— **在生产装配下**验证，不是单元级。

    单元测试（`tests/platform/test_llm.py`）证明的是「两个共享 (base_url, model) 的
    客户端会共享记忆」。但生产里客户端是**每请求新建**的（`main._runtime_llm`），
    所以真正要证的是：跨请求它仍活着。两者之间隔着组装根 —— 若那里的 base_url 因
    归一化差异而键不匹配，单元测试全绿而生产每次调用都重吃 400。

    （这正是本轮修的 bug 的翻版：机制存在，但装配方式让它失效。）
    """

    def test_second_request_skips_rejected_modes(self, tmp_path: Path) -> None:
        import httpx

        from hunter1.domain.settings import LLMSettings
        from hunter1.platform.llm import OpenAICompatibleClient, clear_rejected_modes
        from hunter1.slices.scoring import CandidateProfile
        from hunter1.slices.scoring.store import ScoreStore

        clear_rejected_modes()
        modes: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            fmt = body.get("response_format")
            modes.append("none" if fmt is None else str(fmt.get("type")))
            if fmt is not None:
                # 措辞按**真实形状**写：厂商的 400 会点名出问题的参数，而「是否永久
                # 记住该模式被拒」的判据就看响应体措辞（见 llm._looks_like_format_rejection）。
                # 早先这里写笼统的 "unsupported"，生产逻辑判不出来 —— 假数据不自知地
                # 偏离了真实，会让本用例在真实判据下失去意义。
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": (
                                "Invalid parameter: 'response_format' of type 'json_schema' "
                                "is not supported with this model."
                            ),
                            "param": "response_format",
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "model": "m",
                    "choices": [{"message": {"role": "assistant", "content": '{"score": 55}'}}],
                },
            )

        app = _production_context(tmp_path, "prod-degrade.db")
        db = app.state.context.db
        db.jobs().upsert(_seeded_job())
        ScoreStore(db).save_profile(CandidateProfile(keywords=["集成"]))
        # base_url 故意带尾斜杠：客户端构造时会归一化（strip + 去尾斜杠），
        # 键必须落在归一化后的值上，否则第二次请求找不到记忆。
        db.settings().save_llm(
            LLMSettings(base_url="https://api.example.com/v1/", model="m", api_key="sk-x")
        )
        app.state.context.llm_factory = lambda s: OpenAICompatibleClient(  # type: ignore[assignment]
            base_url=s.base_url,
            api_key=s.api_key,
            model=s.model,
            transport=httpx.MockTransport(handler),
        )

        with TestClient(app) as test_client:
            assert test_client.post(f"/api/scoring/{JOB_ID}").status_code == 200
            first = list(modes)
            modes.clear()
            assert test_client.post(f"/api/scoring/{JOB_ID}").status_code == 200

        assert first == ["json_schema", "json_object", "none"], f"首次降级链不符：{first}"
        assert modes == ["none"], f"第二次请求不该重试已拒模式，实际发出：{modes}"
        db.dispose()
