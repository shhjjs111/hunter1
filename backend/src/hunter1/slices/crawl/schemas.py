"""crawl 切片的 API 模型 —— 跨端契约的源头。

`CrawlRunner.snapshot()` 的领域模型（`CrawlSnapshot` / `SiteProgress`）刻意与
这些 API 模型分开：领域模型管运行时状态，API 模型管**对外契约** ——
前者可以随意重构，后者改一次前端就得跟着改（契约先行）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from hunter1.slices.crawl.runner import CrawlSnapshot, SiteProgress


class SiteProgressView(BaseModel):
    """一个站点在这一轮里的进度。"""

    label: str
    status: str
    fetched: int
    created: int
    updated: int
    error: str | None = None

    @classmethod
    def from_progress(cls, site: SiteProgress) -> SiteProgressView:
        return cls(
            label=site.label,
            status=site.status,
            fetched=site.fetched,
            created=site.created,
            updated=site.updated,
            error=site.error,
        )


class CrawlStatusResponse(BaseModel):
    """抓取进度快照。"""

    running: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    sites: list[SiteProgressView]
    total_fetched: int
    total_created: int

    @classmethod
    def from_snapshot(cls, snapshot: CrawlSnapshot) -> CrawlStatusResponse:
        return cls(
            running=snapshot.running,
            started_at=snapshot.started_at,
            finished_at=snapshot.finished_at,
            error=snapshot.error,
            sites=[SiteProgressView.from_progress(site) for site in snapshot.sites],
            total_fetched=snapshot.total_fetched,
            total_created=snapshot.total_created,
        )


class StartCrawlResponse(BaseModel):
    """启动结果。`started=False` 表示上一轮还在跑（不是错误）。"""

    started: bool


__all__ = ["CrawlStatusResponse", "SiteProgressView", "StartCrawlResponse"]
