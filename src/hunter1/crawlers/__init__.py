"""站点抓取适配器。

- `hunter1.crawlers.base` —— 适配器基类
- `hunter1.crawlers.static_html` —— 通用静态列表页适配器（声明式规格）
- `hunter1.crawlers.guards` —— 拦截/挑战页识别（不静默失败）
- `hunter1.crawlers.registry` —— 已注册站点表 + 构造入口

新增站点：见 `registry.SITES`。
"""

from __future__ import annotations

from hunter1.crawlers.base import BaseCrawler
from hunter1.crawlers.guards import CrawlBlockedError, detect_blocking, ensure_not_blocked
from hunter1.crawlers.registry import (
    SITES,
    SiteDefinition,
    available_sites,
    build_all,
    build_site,
    get_site,
)
from hunter1.crawlers.static_html import ListPageSpec, StaticHtmlCrawler, parse_list_page

__all__ = [
    "SITES",
    "BaseCrawler",
    "CrawlBlockedError",
    "ListPageSpec",
    "SiteDefinition",
    "StaticHtmlCrawler",
    "available_sites",
    "build_all",
    "build_site",
    "detect_blocking",
    "ensure_not_blocked",
    "get_site",
    "parse_list_page",
]
