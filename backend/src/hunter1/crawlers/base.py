"""基础爬虫 —— 适配器共用的骨架。

子类只需实现 `fetch()`；构造 `RawJob` 时用 `_job()` 统一补上 company/source，
避免各适配器各写一遍、各漏一处。
"""

from __future__ import annotations

from hunter1.application.ports import TextFetcher
from hunter1.domain.crawl import RawJob


class BaseCrawler:
    """所有站点适配器的基类。"""

    def __init__(
        self,
        *,
        key: str = "",
        company: str,
        careers_url: str,
        fetcher: TextFetcher,
    ) -> None:
        # key 缺省回退到 company：站点注册表总会给显式 key；手写适配器不传时
        # 保持与旧行为一致（按显示名关联）。两个站点同名时用显式 key 区分。
        self.key = key or company
        self.company = company
        self.careers_url = careers_url
        self.fetcher = fetcher

    def fetch(self) -> list[RawJob]:
        raise NotImplementedError

    def _job(
        self,
        title: str,
        *,
        detail_url: str = "",
        city: str | None = None,
        jd_raw: str | None = None,
        link_kind: str = "detail",
        campaign_text: str | None = None,
        tags: list[str] | None = None,
    ) -> RawJob:
        return RawJob(
            company=self.company,
            title=title,
            detail_url=detail_url or self.careers_url,
            city=city,
            jd_raw=jd_raw,
            link_kind=link_kind,
            source=self.company,
            campaign_text=campaign_text,
            tags=tags or [],
        )


__all__ = ["BaseCrawler"]
