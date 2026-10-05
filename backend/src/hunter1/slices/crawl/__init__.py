"""crawl 切片 —— 抓取编排 + 站点适配 + 进度运行器。

公开面（其他切片只允许从这里 import 符号）：

- `crawl_company` / `crawl_all`：抓取用例
- `CrawlResult` / `BatchCrawlResult`：结果模型
- `SITES` / `SiteDefinition` / `available_sites` / `build_site` / `build_all`：站点注册表
- `ListPageSpec` / `StaticHtmlCrawler` / `BaseCrawler` / `parse_list_page`：适配器
- `CrawlRunner` / `CrawlSnapshot` / `SiteProgress`：进度运行器
- `build_router`：HTTP 面工厂

详见 `SLICE.md`。
"""

from hunter1.slices.crawl.adapters import (
    BaseCrawler,
    ListPageSpec,
    StaticHtmlCrawler,
    parse_list_page,
)
from hunter1.slices.crawl.guards import CrawlBlockedError, detect_blocking, ensure_not_blocked
from hunter1.slices.crawl.router import build_router
from hunter1.slices.crawl.runner import CrawlRunner, CrawlSnapshot, SiteProgress
from hunter1.slices.crawl.service import (
    BatchCrawlResult,
    CrawlResult,
    crawl_all,
    crawl_company,
)
from hunter1.slices.crawl.sites import (
    SITES,
    SiteDefinition,
    available_sites,
    build_all,
    build_site,
    get_site,
)

__all__ = [
    "SITES",
    "BaseCrawler",
    "BatchCrawlResult",
    "CrawlBlockedError",
    "CrawlResult",
    "CrawlRunner",
    "CrawlSnapshot",
    "ListPageSpec",
    "SiteDefinition",
    "SiteProgress",
    "StaticHtmlCrawler",
    "available_sites",
    "build_all",
    "build_router",
    "build_site",
    "crawl_all",
    "crawl_company",
    "detect_blocking",
    "ensure_not_blocked",
    "get_site",
    "parse_list_page",
]
