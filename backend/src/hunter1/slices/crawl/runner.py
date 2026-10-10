"""抓取进度运行器。

在后台线程里跑一轮多站点抓取，并把**逐站进度**暴露成快照供界面轮询。

两条不能破的约定：
- **一次只跑一轮**：`start()` 在已有任务在跑时返回 False。并发压同一批站点
  既是给站点添堵，也会让进度快照互相覆盖。
- **任何异常都要收敛**：装配失败、抓取失败，都必须把 `running` 置回 False。
  否则进度页会永远显示「正在抓取」，用户只能重启进程 —— 这正是旧系统
  「静默失败」的老毛病换了个地方复发。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from hunter1.application.ports import Crawler, JobRepository
from hunter1.slices.crawl.service import CrawlResult, crawl_all


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SiteProgress:
    """一个站点在这一轮里的进度。"""

    label: str
    key: str = ""  # 站点唯一标识（进度关联用）；label 只用于显示
    status: str = "pending"  # pending | running | ok | failed
    fetched: int = 0
    created: int = 0
    updated: int = 0
    error: str | None = None


@dataclass
class CrawlSnapshot:
    """某一时刻的进度快照。"""

    running: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    sites: list[SiteProgress] = field(default_factory=list)

    @property
    def total_fetched(self) -> int:
        return sum(site.fetched for site in self.sites)

    @property
    def total_created(self) -> int:
        return sum(site.created for site in self.sites)

    @property
    def failed(self) -> list[SiteProgress]:
        return [site for site in self.sites if site.status == "failed"]


class CrawlRunner:
    """一轮抓取的任务与进度。线程安全。"""

    def __init__(
        self,
        *,
        crawler_factory: Callable[[], list[Crawler]],
        jobs: JobRepository,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._crawler_factory = crawler_factory
        self._jobs = jobs
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CrawlSnapshot(running=False)
        self._thread: threading.Thread | None = None

    # ---- 对外 ----

    def start(self) -> bool:
        """非阻塞启动一轮；已有任务在跑返回 False，线程起不来则抛 `RuntimeError`。

        抛错前会把 `running` 回滚成 False —— 否则进度页会永远停在「正在抓取」。
        调用方（HTTP 面）负责把它映射成可读的失败，见 `slices/crawl/router.py`。
        """
        with self._lock:
            if self._state.running:
                return False
            self._state = CrawlSnapshot(running=True, started_at=self._clock())
        self._thread = threading.Thread(target=self._guarded_run, name="hunter1-crawl", daemon=True)
        try:
            self._thread.start()
        except BaseException as exc:
            # 线程起不来（`RuntimeError: can't start new thread` —— 线程/句柄耗尽）时
            # `_guarded_run` 从未执行，没人会把 running 置回 False：进度页永远停在
            # 「正在抓取」，`start()` 从此恒返回 False，只能重启进程。
            # 这正是本模块开头那条「任何异常都要收敛」要防的情形，只是它发生在
            # **进入**线程之前，`_guarded_run` 里的 try 覆盖不到。所以在这里回滚置位，
            # 再把异常按原样抛出去（异常形状是开放集合，用 BaseException 一并兜住）。
            with self._lock:
                self._state = CrawlSnapshot(running=False, error=f"线程启动失败：{exc}")
            raise
        return True

    def run(self) -> None:
        """同步跑完一轮（测试与 CLI 用）。"""
        with self._lock:
            if self._state.running:
                raise RuntimeError("a crawl is already running")
            self._state = CrawlSnapshot(running=True, started_at=self._clock())
        self._guarded_run()

    def snapshot(self) -> CrawlSnapshot:
        with self._lock:
            return CrawlSnapshot(
                running=self._state.running,
                started_at=self._state.started_at,
                finished_at=self._state.finished_at,
                error=self._state.error,
                sites=[replace(site) for site in self._state.sites],
            )

    # ---- 内部 ----

    def _guarded_run(self) -> None:
        """无论中间出什么事，最后一定要把 running 收掉。"""
        try:
            crawlers = list(self._crawler_factory())
            with self._lock:
                self._state.sites = [
                    SiteProgress(label=crawler.company, key=crawler.key, status="running")
                    for crawler in crawlers
                ]
            crawl_all(crawlers, jobs=self._jobs, now=self._clock(), on_result=self._record)
            # **正常结束**也要收敛：某个 key 没等到结果时（例如同一站点被写了两次、
            # 结果只够结算一行），那一行会永远停在「抓取中」。原先只在 except 分支
            # 收敛，这条路径漏了 —— 用户看到的是「跑完了但仍有一行在转」。
            with self._lock:
                self._settle_leftover_sites("整轮结束但未收到该站点的抓取结果")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._state.error = message
                # 还停在 running 的站点一律标成 failed —— 开场就把所有站点置为
                # running，中途异常若不收敛，界面会**永远**显示「抓取中」，
                # 失败列表里也看不到它（用户只能重启进程脱困）。这里与
                # `crawl_company` 的逐站收敛是两道独立的保险：那一道管单个站点，
                # 这一道管整轮被打断。
                self._settle_leftover_sites(message)
        finally:
            with self._lock:
                self._state.running = False
                self._state.finished_at = self._clock()

    def _settle_leftover_sites(self, message: str) -> None:
        """把仍停在 `running` 的行收敛成失败（调用方需持有 `self._lock`）。"""
        for site in self._state.sites:
            if site.status == "running":
                site.status = "failed"
                site.error = message

    def _record(self, result: CrawlResult) -> None:
        with self._lock:
            candidates = [site for site in self._state.sites if site.key == result.site_key]
            if not candidates:
                return
            # 同一 key 出现多行时（用户把同一个站点写了两次），结果要**依次**落到
            # 还没结算的那一行 —— 原实现命中首个就 return，第二行永远停在 running；
            # 而按名字匹配又会把第二个站点的结果记到第一行（key 才是身份）。
            target = next((site for site in candidates if site.status == "running"), candidates[-1])
            target.status = "ok" if result.ok else "failed"
            target.fetched = result.fetched
            target.created = result.created
            target.updated = result.updated
            target.error = result.error


__all__ = ["CrawlRunner", "CrawlSnapshot", "SiteProgress"]
