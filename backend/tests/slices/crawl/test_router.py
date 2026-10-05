"""crawl 切片 HTTP 面测试 —— 真 SQLite + TestClient + 假抓取器，全程离线。

只挂本切片的 router（不带 web 层），验证它作为独立单元能跑。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hunter1.domain.crawl import RawJob
from hunter1.platform.db import Database
from hunter1.slices.crawl.router import build_router
from hunter1.slices.crawl.runner import CrawlRunner

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class FakeCrawler:
    def __init__(self, company: str, *, count: int = 1, boom: bool = False, key: str = "") -> None:
        self.key = key or company
        self.company = company
        self.careers_url = f"https://{company}.example.com/jobs"
        self._count = count
        self._boom = boom

    def fetch(self) -> list[RawJob]:
        if self._boom:
            raise RuntimeError("boom")
        return [
            RawJob(
                company=self.company,
                title=f"{self.company}-岗位{index}",
                detail_url=f"https://{self.company}.example.com/j/{index}",
            )
            for index in range(self._count)
        ]


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "crawl-api.db")
    database.initialize()
    return database


@pytest.fixture()
def client(db: Database) -> Iterator[TestClient]:
    crawlers = [FakeCrawler("甲", count=2), FakeCrawler("乙", boom=True)]
    runner = CrawlRunner(crawler_factory=lambda: list(crawlers), jobs=db.jobs(), clock=lambda: NOW)
    app = FastAPI()
    app.include_router(build_router(runner=runner), prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


class TestStartEndpoint:
    def test_start_crawl_returns_started_true(self, client: TestClient) -> None:
        response = client.post("/api/crawl")
        assert response.status_code == 200
        assert response.json() == {"started": True}

    def test_second_start_is_refused_without_error(self) -> None:
        """已在跑时返回 started=false —— 不抛错，让调用方按标志分支。

        用**慢抓取器**独占这个断言：第一次抓取可能在几毫秒内跑完，
        那时第二次 POST 拿到 True 是正确行为（上一轮确实结束了），
        断言就会变成竞态误报。
        """
        import time

        from hunter1.slices.crawl.runner import CrawlRunner

        class SlowCrawler(FakeCrawler):
            def fetch(self) -> list[RawJob]:
                time.sleep(0.6)
                return super().fetch()

        with TemporaryDirectory() as workspace:
            database = Database(Path(workspace) / "slow.db")
            database.initialize()
            runner = CrawlRunner(
                crawler_factory=lambda: [SlowCrawler("慢站")],
                jobs=database.jobs(),
                clock=lambda: NOW,
            )
            app = FastAPI()
            app.include_router(build_router(runner=runner), prefix="/api")
            with TestClient(app) as test_client:
                assert test_client.post("/api/crawl").json() == {"started": True}
                # 第一轮仍在跑（抓取器 sleep 0.6s）→ 第二次必须被拒
                assert test_client.post("/api/crawl").json() == {"started": False}
            database.dispose()


class TestStatusEndpoint:
    def test_status_before_run(self, client: TestClient) -> None:
        payload = client.get("/api/crawl/status").json()
        assert payload["running"] is False
        assert payload["sites"] == []
        assert payload["total_fetched"] == 0

    def test_status_after_run_reports_each_site(self, client: TestClient) -> None:
        client.post("/api/crawl")
        # 等后台线程收敛（daemon 线程，最多几毫秒）
        import time

        deadline = time.monotonic() + 10
        while client.get("/api/crawl/status").json()["running"] and time.monotonic() < deadline:
            time.sleep(0.02)

        payload = client.get("/api/crawl/status").json()
        assert payload["running"] is False
        assert [site["label"] for site in payload["sites"]] == ["甲", "乙"]
        # 甲成功 2 条、乙失败（boom）—— 单站失败不中断整轮
        assert payload["sites"][0]["status"] == "ok"
        assert payload["sites"][0]["fetched"] == 2
        assert payload["sites"][1]["status"] == "failed"
        assert "boom" in payload["sites"][1]["error"]
        assert payload["total_fetched"] == 2

    def test_status_is_json_serializable(self, client: TestClient) -> None:
        client.post("/api/crawl")
        import time

        time.sleep(0.3)
        payload = client.get("/api/crawl/status").json()
        assert isinstance(payload["total_created"], int)
        assert isinstance(payload["sites"], list)
