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

from hunter1.application.crawl import CrawlResult, crawl_all
from hunter1.application.ports import Crawler, JobRepository


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

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "status": self.status,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "error": self.error,
        }


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

    def as_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
            "sites": [site.as_dict() for site in self.sites],
            "total_fetched": self.total_fetched,
            "total_created": self.total_created,
        }


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
        """非阻塞启动一轮；已有任务在跑则返回 False。"""
        with self._lock:
            if self._state.running:
                return False
            self._state = CrawlSnapshot(running=True, started_at=self._clock())
        self._thread = threading.Thread(target=self._guarded_run, name="hunter1-crawl", daemon=True)
        self._thread.start()
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
        except Exception as exc:
            with self._lock:
                self._state.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._state.running = False
                self._state.finished_at = self._clock()

    def _record(self, result: CrawlResult) -> None:
        with self._lock:
            for site in self._state.sites:
                # 按 key 关联而不是显示名：两个站点恰好同名时，按名字匹配会把
                # 第二个站点的结果记到第一行，第二行永远停在 running。
                if site.key != result.site_key:
                    continue
                site.status = "ok" if result.ok else "failed"
                site.fetched = result.fetched
                site.created = result.created
                site.updated = result.updated
                site.error = result.error
                return


__all__ = ["CrawlRunner", "CrawlSnapshot", "SiteProgress"]
