"""抓取进度运行器测试 —— 不联网，用假抓取器。

重点不是「能跑」，而是**异常路径不能把状态卡在 running**：
本地工具的进度页若因为一次异常永远显示「正在抓取」，用户就只能重启进程。

（从旧 `tests/web/test_crawl_runner.py` 迁移：断言语义逐条保留。）
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from hunter1.domain.crawl import RawJob
from hunter1.domain.models import CaptureStatus
from hunter1.platform.db import Database
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
def db(tmp_path) -> Database:  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "runner.db")
    database.initialize()
    return database


def _runner(db: Database, crawlers: list[FakeCrawler]) -> CrawlRunner:
    return CrawlRunner(crawler_factory=lambda: list(crawlers), jobs=db.jobs(), clock=lambda: NOW)


class TestRun:
    def test_records_each_site(self, db: Database) -> None:
        runner = _runner(db, [FakeCrawler("甲", count=2), FakeCrawler("乙")])
        runner.run()
        snapshot = runner.snapshot()
        assert snapshot.running is False
        assert [site.label for site in snapshot.sites] == ["甲", "乙"]
        assert [site.status for site in snapshot.sites] == ["ok", "ok"]
        assert snapshot.sites[0].fetched == 2
        assert snapshot.total_fetched == 3
        assert db.jobs().count() == 3

    def test_failure_marks_site_failed_but_continues(self, db: Database) -> None:
        runner = _runner(db, [FakeCrawler("甲", boom=True), FakeCrawler("乙")])
        runner.run()
        snapshot = runner.snapshot()
        assert [site.status for site in snapshot.sites] == ["failed", "ok"]
        assert "boom" in (snapshot.sites[0].error or "")
        assert snapshot.total_fetched == 1

    def test_same_label_sites_are_tracked_separately(self, db: Database) -> None:
        """两个站点恰好同名时进度不能串行 —— 关联用 key，显示才用 label。

        旧实现按 label 匹配：第二个同名站点的结果会被记到第一行，
        第二行永远停在 running（页面看起来「卡住了」）。
        """
        first = FakeCrawler("同名站", count=2, key="site_a")
        second = FakeCrawler("同名站", count=1, key="site_b")
        runner = _runner(db, [first, second])
        runner.run()
        snapshot = runner.snapshot()
        assert [site.status for site in snapshot.sites] == ["ok", "ok"]
        assert snapshot.sites[0].fetched == 2
        assert snapshot.sites[1].fetched == 1

    def test_factory_failure_does_not_leave_it_running(self, db: Database) -> None:
        """装配阶段就炸了也必须收敛到「已结束」——否则进度页永远转圈。"""

        def boom_factory() -> list[FakeCrawler]:
            raise RuntimeError("装配失败")

        runner = CrawlRunner(crawler_factory=boom_factory, jobs=db.jobs(), clock=lambda: NOW)
        runner.run()
        snapshot = runner.snapshot()
        assert snapshot.running is False
        assert snapshot.error is not None
        assert "装配失败" in snapshot.error
        assert snapshot.sites == []

    def test_snapshot_before_any_run(self, db: Database) -> None:
        runner = _runner(db, [])
        snapshot = runner.snapshot()
        assert snapshot.running is False
        assert snapshot.sites == []
        assert snapshot.started_at is None

    def test_jobs_land_with_pending_capture_status(self, db: Database) -> None:
        runner = _runner(db, [FakeCrawler("甲")])
        runner.run()
        jobs = db.jobs().list()
        assert len(jobs) == 1
        assert jobs[0].capture_status is CaptureStatus.PENDING

    def test_as_dict_is_json_friendly(self, db: Database) -> None:
        runner = _runner(db, [FakeCrawler("甲")])
        runner.run()
        payload = runner.snapshot().as_dict()
        assert payload["running"] is False
        assert payload["sites"][0]["label"] == "甲"  # type: ignore[index]
        assert isinstance(payload["sites"][0]["fetched"], int)  # type: ignore[index]


class TestStart:
    def test_start_refuses_when_already_running(self, db: Database) -> None:
        runner = _runner(db, [FakeCrawler("甲")])
        # 手动占住 running，模拟「上一轮还没跑完」
        assert runner.start() is True
        assert runner.start() is False  # 第二轮被拒，不并发压同一个站点

    def test_start_finishes_in_background(self, db: Database) -> None:
        runner = _runner(db, [FakeCrawler("甲"), FakeCrawler("乙")])
        assert runner.start() is True
        deadline = time.monotonic() + 10
        while runner.snapshot().running and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = runner.snapshot()
        assert snapshot.running is False
        assert len(snapshot.sites) == 2

    def test_can_restart_after_finish(self, db: Database) -> None:
        runner = _runner(db, [FakeCrawler("甲")])
        runner.run()
        assert runner.start() is True
