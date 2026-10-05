"""站点适配器 —— 基础爬虫骨架 + 声明式静态列表页适配器。

（本文件由旧 `crawlers/base.py` 与 `crawlers/static_html.py` 合并而来：
两者是同一件事的两半 —— 骨架与它最常见的实现，拆开只会让读者多跳一次文件。）

两类站点都覆盖：

- **单一雇主**（公司自己的招聘页）：`ListPageSpec.company_selector=None`，
  页面里不必有公司名。
- **岗位聚合站**（实习僧 / 猎聘 / BOSS 等）：一页里每条的雇主都不同 ——
  用 `company_selector` 逐条抽取，抽不到才回落到调用方给的默认值。

分页约定：`url_template` 里含 `{page}` 占位符时按页递增，直到某页无岗位
或达到 `max_pages`；不含占位符则只抓一页。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from hunter1.application.ports import TextFetcher
from hunter1.domain.crawl import RawJob, normalize_detail_url
from hunter1.slices.crawl.guards import ensure_not_blocked


class BaseCrawler:
    """所有站点适配器的基类。

    子类只需实现 `fetch()`；构造 `RawJob` 时用 `_job()` 统一补上 company/source，
    避免各适配器各写一遍、各漏一处。
    """

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


@dataclass(frozen=True)
class ListPageSpec:
    """一个静态列表页的解析规格。

    字段里只有 `url_template` / `item_selector` / `title_selector` 是必需的；
    其余按站点实际情况填 —— 选择器写 None 表示该站点没有这个维度的信息，
    解析时该字段留空，**不猜测、不用邻近节点的文本凑**。
    """

    url_template: str
    item_selector: str
    title_selector: str
    link_selector: str | None = None  # None：先看条目自身是不是 <a>，再取条目内第一个 <a>
    city_selector: str | None = None
    company_selector: str | None = None  # None：整站用同一个公司（单一雇主站）
    max_pages: int = 1
    job_type: str = "校招"
    link_kind: str = "detail"

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
    - `company` 是**默认雇主**；`spec.company_selector` 命中时按条目覆盖
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

        href = _item_href(item, spec.link_selector)
        if not href:
            continue

        detail_url = normalize_detail_url(urljoin(page_url, href))
        if detail_url in seen:
            continue
        seen.add(detail_url)

        jobs.append(
            RawJob(
                company=_item_company(item, spec, default=company),
                title=title,
                detail_url=detail_url,
                city=_item_text(item, spec.city_selector),
                job_type=spec.job_type,
                link_kind=spec.link_kind,
                source=company,
            )
        )

    return jobs


def _item_href(item: Tag, link_selector: str | None) -> str:
    """取条目里的链接。

    顺序：显式 `link_selector` → 条目自身的 href → 条目内第一个 `<a>`。
    中间那步是为「卡片本身就是 `<a>`」的站点（如猎聘）准备的 ——
    这类站点用 `item.find("a")` 找不到东西，因为链接就是它自己。
    """
    node: Tag | None = None
    if link_selector:
        node = item.select_one(link_selector)
    else:
        own = item.get("href")
        if isinstance(own, str) and own.strip():
            node = item
    if node is None:
        node = item.find("a")
    if node is None:
        return ""
    href = node.get("href")
    return href.strip() if isinstance(href, str) else ""


def _item_text(item: Tag, selector: str | None) -> str | None:
    """抽一个「单值」字段（城市 / 雇主）的文本。

    真实页面里这类字段常有三种写法，按固定优先级取：

    1. **可见文本的首个非空行** —— 很多站点的城市节点里连带着学历/经验
       （`武汉-洪山区\\n本科\\n1年以上`），整块取回会把三样拼成一个"城市"。
    2. 文本为空时退到 `title` 属性 —— 雇主位置常只有一个 logo 图，
       名字挂在 `<a title="...">` 上。
    3. 再退到后代 `<img>` 的 `alt`。

    都不命中则返回 None（留空），**不用邻近节点凑**。
    """
    if not selector:
        return None
    node = item.select_one(selector)
    if node is None:
        return None

    for line in node.get_text().splitlines():
        stripped = line.strip()
        if stripped:
            return stripped

    # 整个子树都没有可见文字：退到属性（自身优先，再按文档序找后代）。
    for candidate in (node, *node.find_all(True)):
        for attr in ("title", "alt"):
            value = candidate.get(attr)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _item_company(item: Tag, spec: ListPageSpec, *, default: str) -> str:
    """按条目抽雇主；抽不到时回落到默认雇主（绝不产出空公司名）。"""
    return _item_text(item, spec.company_selector) or default


class StaticHtmlCrawler(BaseCrawler):
    """按 `ListPageSpec` 抓取静态列表页的适配器。"""

    def __init__(
        self,
        *,
        company: str,
        careers_url: str,
        spec: ListPageSpec,
        fetcher: TextFetcher,
        key: str = "",
    ) -> None:
        super().__init__(key=key, company=company, careers_url=careers_url, fetcher=fetcher)
        self.spec = spec

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        seen: set[str] = set()
        max_pages = max(1, self.spec.max_pages if self.spec.is_paginated else 1)

        for page in range(1, max_pages + 1):
            url = self.spec.url_for(page)
            html = self.fetcher.get_text(url)
            # 拦截页会伪装成「没有岗位的页面」——必须在解析前拦下，
            # 否则会静默返回 0 条（见 guards 模块的说明）。
            ensure_not_blocked(html, url=url)
            page_jobs = parse_list_page(html, self.spec, company=self.company, page_url=url)
            if not page_jobs:
                break  # 空页 = 没有更多了
            for job in page_jobs:
                if job.detail_url in seen:
                    continue
                seen.add(job.detail_url)
                jobs.append(job)

        return jobs


__all__ = ["BaseCrawler", "ListPageSpec", "StaticHtmlCrawler", "parse_list_page"]
