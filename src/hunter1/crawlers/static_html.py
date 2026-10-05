"""通用静态 HTML 列表页适配器。

适用于「列表页是服务端渲染的 HTML、岗位链接在列表里」的一大类站点
（旧系统里这类占了纯 HTTP 适配器的多数）。用**声明式规格**描述选择器，
不必为每个站点写一个 Python 文件。

分页约定：`url_template` 里含 `{page}` 占位符时按页递增，直到某页无岗位
或达到 `max_pages`；不含占位符则只抓一页。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from hunter1.application.ports import TextFetcher
from hunter1.crawlers.base import BaseCrawler
from hunter1.domain.crawl import RawJob, normalize_detail_url


@dataclass(frozen=True)
class ListPageSpec:
    """一个静态列表页的解析规格。"""

    url_template: str
    item_selector: str
    title_selector: str
    link_selector: str | None = None  # None 时取 item 内第一个 <a>
    city_selector: str | None = None
    max_pages: int = 1

    @property
    def is_paginated(self) -> bool:
        return "{page}" in self.url_template

    def url_for(self, page: int) -> str:
        return self.url_template.format(page=page)


def parse_list_page(
    html: str,
    spec: ListPageSpec,
    *,
    company: str,
    page_url: str,
) -> list[RawJob]:
    """把一页 HTML 解析为 `RawJob` 列表（纯函数，可离线测试）。

    - 相对链接按 `page_url` 解析为绝对链接
    - 缺标题或缺链接的条目跳过（不产出半成品）
    - 同页按归一化 URL 去重
    """
    soup = BeautifulSoup(html, "lxml")
    jobs: list[RawJob] = []
    seen: set[str] = set()

    for item in soup.select(spec.item_selector):
        title_node = item.select_one(spec.title_selector)
        if title_node is None:
            continue
        title = title_node.get_text(strip=True)
        if not title:
            continue

        link_node = item.select_one(spec.link_selector) if spec.link_selector else item.find("a")
        href = (link_node.get("href") if link_node is not None else None) or ""
        href = href.strip() if isinstance(href, str) else ""
        if not href:
            continue

        detail_url = normalize_detail_url(urljoin(page_url, href))
        if detail_url in seen:
            continue
        seen.add(detail_url)

        city: str | None = None
        if spec.city_selector:
            city_node = item.select_one(spec.city_selector)
            if city_node is not None:
                city = city_node.get_text(strip=True) or None

        jobs.append(
            RawJob(
                company=company,
                title=title,
                detail_url=detail_url,
                city=city,
                source=company,
            )
        )

    return jobs


class StaticHtmlCrawler(BaseCrawler):
    """按 `ListPageSpec` 抓取静态列表页的适配器。"""

    def __init__(
        self,
        *,
        company: str,
        careers_url: str,
        spec: ListPageSpec,
        fetcher: TextFetcher,
    ) -> None:
        super().__init__(company=company, careers_url=careers_url, fetcher=fetcher)
        self.spec = spec

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        seen: set[str] = set()
        max_pages = max(1, self.spec.max_pages if self.spec.is_paginated else 1)

        for page in range(1, max_pages + 1):
            url = self.spec.url_for(page)
            html = self.fetcher.get_text(url)
            page_jobs = parse_list_page(html, self.spec, company=self.company, page_url=url)
            if not page_jobs:
                break  # 空页 = 没有更多了
            for job in page_jobs:
                if job.detail_url in seen:
                    continue
                seen.add(job.detail_url)
                jobs.append(job)

        return jobs


__all__ = ["ListPageSpec", "StaticHtmlCrawler", "parse_list_page"]
