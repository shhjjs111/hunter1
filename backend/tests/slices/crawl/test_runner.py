"""抓取进度运行器测试 —— 不联网，用假抓取器。

重点不是「能跑」，而是**异常路径不能把状态卡在 running**：
本地工具的进度页若因为一次异常永远显示「正在抓取」，用户就只能重启进程。

（从旧 `tests/web/test_crawl_runner.py` 迁移：断言语义逐条保留。）
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest

from hunter1.domain.crawl import RawJob
from hunter1.domain.models import CaptureStatus
from hunter1.platform.db import Database
from hunter1.slices.crawl.runner import CrawlRunner
from hunter1.slices.crawl.schemas import CrawlStatusResponse
from hunter1.slices.crawl.service import CrawlResult

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

    def test_records_created_and_updated_per_site(self, db: Database) -> None:
        """逐站的「新增/更新」要真的落到快照上 —— 抓取页那几列直接读它。

        实测：把 `_record` 里 `target.created = result.created` 改成 0，125 条 crawl
        用例**全绿** —— 此前只断言过 `fetched`。而「抓到 3 条」看不出有没有重复
        入库，新增/更新这两列才是用户判断这一轮干了什么的东西。
        """
        runner = _runner(db, [FakeCrawler("甲", count=2), FakeCrawler("乙")])
        runner.run()
        first = runner.snapshot()
        assert [site.fetched for site in first.sites] == [2, 1]
        assert [site.created for site in first.sites] == [2, 1]
        assert [site.updated for site in first.sites] == [0, 0]
        assert first.total_created == 3

        # 再抓一轮：同样两个岗位 → 不新增、全算更新（幂等 upsert 的对外证据）
        runner.run()
        again = runner.snapshot()
        assert [site.created for site in again.sites] == [0, 0]
        assert [site.updated for site in again.sites] == [2, 1]
        assert db.jobs().count() == 3

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

    def test_interrupted_round_does_not_leave_sites_running(self, db: Database) -> None:
        """整轮被打断时，已置为 running 的站点不能永远停在「抓取中」。

        开场就把所有站点置为 `running`；中途异常若不收敛，进度页对那个站点
        **永远**显示「抓取中」、失败列表里也看不到它 —— 用户只能重启进程
        （这正是旧系统「静默失败」的老毛病换了个地方）。

        触发点用「批次时钟抛错」：站点已置位之后、逐站结果之前的这段窗口，
        在 `crawl_company` 已收编落库异常（见 test_service）之后，是剩下的
        逃逸路径。断言的是**收敛行为**，不在于是哪种异常。
        """
        calls = {"n": 0}

        def flaky_clock() -> datetime:
            calls["n"] += 1
            if calls["n"] == 2:  # 第一次是 started_at，第二次是整批的时间戳
                raise RuntimeError("时钟炸了")
            return NOW

        runner = CrawlRunner(
            crawler_factory=lambda: [FakeCrawler("甲"), FakeCrawler("乙")],
            jobs=db.jobs(),
            clock=flaky_clock,
        )
        runner.run()

        snapshot = runner.snapshot()
        assert snapshot.running is False
        assert snapshot.error is not None and "时钟炸了" in snapshot.error
        assert [site.status for site in snapshot.sites] == ["failed", "failed"]
        assert [site.label for site in snapshot.failed] == ["甲", "乙"]

    def test_jobs_land_with_pending_capture_status(self, db: Database) -> None:
        runner = _runner(db, [FakeCrawler("甲")])
        runner.run()
        jobs = db.jobs().list()
        assert len(jobs) == 1
        assert jobs[0].capture_status is CaptureStatus.PENDING

    def test_duplicate_site_keys_all_settle(self, db: Database) -> None:
        """同一 key 被写了两次（`--sites a,a`）时，两行都要结算。

        原实现 `_record` 命中首个匹配就 return —— 第二行永远停在「抓取中」，直到
        用户重启进程（快照的 running 却是 False，更让人困惑）。
        """
        runner = _runner(db, [FakeCrawler("甲", key="dup"), FakeCrawler("甲", key="dup")])
        runner.run()

        snapshot = runner.snapshot()
        assert [site.status for site in snapshot.sites] == ["ok", "ok"]
        assert not [site for site in snapshot.sites if site.status == "running"]

    def test_missing_result_is_converged_on_normal_finish(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """整轮正常结束但某一行没等到结果 → 也要收敛（不能停在「抓取中」）。

        原先只在 except 分支收敛，这条路漏了：用户看到「跑完了」但有一行还在转。
        """
        import hunter1.slices.crawl.runner as runner_module

        def only_first(crawlers, *, jobs, now, on_result):
            on_result(CrawlResult(company="乙", site_key="乙", fetched=1))  # 无 error 即 ok

        monkeypatch.setattr(runner_module, "crawl_all", only_first)
        runner = _runner(db, [FakeCrawler("甲", key="甲"), FakeCrawler("乙", key="乙")])
        runner.run()

        snapshot = runner.snapshot()
        statuses = {site.key: site.status for site in snapshot.sites}
        assert statuses == {"甲": "failed", "乙": "ok"}
        leftover = next(site for site in snapshot.sites if site.key == "甲")
        assert leftover.error is not None and "未收到" in leftover.error

    def test_snapshot_round_trips_through_the_response_model(self, db: Database) -> None:
        """快照只经**一个**序列化器（生产用的 `CrawlStatusResponse`）交给外界。

        原先 `SiteProgress` / `CrawlSnapshot` 各另有一个 `as_dict`，只有测试消费 ——
        同一份形状两处定义，改一处忘另一处时测试仍绿、生产与测试缓慢漂移（本仓库
        删 application 层五个拷贝时记下的同一条教训）。这里钉住唯一路径，并顺带
        验证它对 JSON 友好（时间字段能被 pydantic 序列化）。
        """
        runner = _runner(db, [FakeCrawler("甲")])
        runner.run()
        response = CrawlStatusResponse.from_snapshot(runner.snapshot())
        payload = response.model_dump(mode="json")

        assert payload["running"] is False
        assert payload["sites"][0]["label"] == "甲"
        assert isinstance(payload["sites"][0]["fetched"], int)
        assert payload["total_fetched"] == 1


class TestStart:
    def test_start_refuses_when_already_running(self, db: Database) -> None:
        """已在跑时第二次 `start()` 必须被拒 —— 不并发压同一个站点。

        用**闸门抓取器**把第一轮钉在「正在跑」这一刻：`FakeCrawler` 是瞬时返回的，
        第二次 `start()` 完全可能在后台线程跑完之后才执行 —— 那时返回 True 是
        正确行为（上一轮确实结束了），断言就成了竞态误报。
        （同 `test_router` 的 SlowCrawler 手法；这里用 Event 更确定，不靠 sleep 时长。）
        """
        gate = threading.Event()
        entered = threading.Event()

        class GatedCrawler(FakeCrawler):
            def fetch(self) -> list[RawJob]:
                entered.set()
                gate.wait(timeout=10)
                return super().fetch()

        runner = _runner(db, [GatedCrawler("甲")])
        try:
            assert runner.start() is True
            assert entered.wait(timeout=10)  # 确认第一轮真的进了抓取
            assert runner.start() is False  # 此刻第二轮必须被拒
        finally:
            gate.set()  # 放行后台线程，别把 daemon 挂在闸门上

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

    def test_thread_start_failure_does_not_leave_it_running(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """线程起不来也必须收敛 —— 这条路径在 `_guarded_run` **之前**，它兜不住。

        `Thread.start()` 抛 `RuntimeError: can't start new thread`（线程/句柄耗尽）时
        `running=True` 已经置位，而 `_guarded_run` 从未执行：没有任何人会把它置回来。
        后果是进度页永远显示「正在抓取」、`start()` 从此恒返回 False，只能重启进程 ——
        正是模块开头「任何异常都要收敛」那条约定要防的情形。
        """
        import types

        import hunter1.slices.crawl.runner as runner_module

        class UnstartableThread:
            """替身：构造得出来，`start()` 必炸（真实场景是线程/句柄耗尽）。"""

            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def start(self) -> None:
                raise RuntimeError("can't start new thread")

        runner = _runner(db, [FakeCrawler("甲")])
        # 只换 runner 模块眼里的 threading，不去 patch 真的 Thread.start ——
        # 那会波及 pytest 自己的线程。
        monkeypatch.setattr(
            runner_module, "threading", types.SimpleNamespace(Thread=UnstartableThread)
        )

        with pytest.raises(RuntimeError, match="can't start new thread"):
            runner.start()

        snapshot = runner.snapshot()
        assert snapshot.running is False, "线程没起来却留着 running=True → 永久卡在「正在抓取」"
        assert snapshot.error is not None
        assert "线程启动失败" in snapshot.error
