"""站点注册表 —— 「新增一个站点」的落点。

用法：

    from hunter1.crawlers.registry import available_sites, build_all

    for crawler in build_all(fetcher=http):
        crawl_company(crawler, jobs=db.jobs())

设计取向（与规划 §3.1「新增站点 = 新增一个文件 + 注册一行」的关系）：
本项目的站点绝大多数是**服务端渲染的列表页**，用 `ListPageSpec` 就能描述。
因此这里的落点是「在 `SITES` 里加一条 `SiteDefinition`」——比「一个新文件」
还少，且选择器集中在一处、便于对照 fixtures 回归。只有真正无法用声明式规格
表达的站点（需要登录、JSON 接口、多步跳转）才需要写自定义 `BaseCrawler` 子类，
用 `SiteDefinition.crawler_class` 挂进来。

选择器的可核销依据：每个站点的选择器都对**真实页面**核对过，
并在 `tests/crawlers/fixtures/<key>.html` 留了 HTML 快照供离线回归
（见 `tests/crawlers/test_site_registry.py`）。

合规提示：这些站点只抓**公开列表页**上的岗位信息，不做登录、不绕验证码、
不做自动投递（与规划 §9 一致）。`max_pages` 刻意压低，避免给站点压力。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from hunter1.application.ports import Crawler, TextFetcher
from hunter1.crawlers.static_html import ListPageSpec, StaticHtmlCrawler


@dataclass(frozen=True)
class SiteDefinition:
    """一个站点的抓取定义。

    `label` 是**默认来源名**：聚合站里每条的雇主由 `company_selector` 逐条覆盖，
    覆盖不到（或列表本身就没有雇主字段）时回落到它。
    """

    key: str
    label: str
    spec: ListPageSpec
    crawler_class: type[StaticHtmlCrawler] = StaticHtmlCrawler

    @property
    def careers_url(self) -> str:
        """第一页地址，供 `Crawler` 协议使用。"""
        return self.spec.url_for(1)


SITES: dict[str, SiteDefinition] = {
    # 实习僧 · 实习岗位列表（服务端渲染，20 条/页，`?page=N` 翻页）
    "shixiseng": SiteDefinition(
        key="shixiseng",
        label="实习僧",
        spec=ListPageSpec(
            url_template="https://www.shixiseng.com/interns?page={page}",
            item_selector=".intern-wrap",
            title_selector="a.title.font",
            link_selector="a.title.font",
            city_selector="span.city",
            company_selector=".f-r .title",
            max_pages=2,
            job_type="实习",
        ),
    ),
    # 猎聘 · 校园招聘岗（卡片外层含职位 + 公司两块，故条目取外层）
    "liepin_campus": SiteDefinition(
        key="liepin_campus",
        label="猎聘校园",
        spec=ListPageSpec(
            url_template="https://www.liepin.com/campus/",
            item_selector=".campus-recommend-jobs-item",
            title_selector=".jobcard2-title",
            link_selector="a.jobcard2-detail-box",
            city_selector=".jobcard2-detail-des span",
            company_selector=".jobcard2-company-title",
        ),
    ),
    # 高校人才网 · 教职/科研岗（`li` 内含职位、学历要求与单位、城市）
    "gaoxiaojob": SiteDefinition(
        key="gaoxiaojob",
        label="高校人才网",
        spec=ListPageSpec(
            url_template="https://www.gaoxiaojob.com/",
            item_selector=".job-board .list ul li",
            title_selector=".position h6",
            link_selector="a",
            city_selector=".region .city",
            company_selector=".region .school",
        ),
    ),
    # BOSS直聘 · 首页推荐岗位卡（岗位与公司在同一 <li> 的两块里）
    # ⚠️ 该站有风控：连续请求后返回 HTTP 200 的「安全验证」页。此时抓取器会抛
    # `CrawlBlockedError`（不静默返回 0 条），需人工介入或改用浏览器适配器（M7）。
    "zhipin": SiteDefinition(
        key="zhipin",
        label="BOSS直聘",
        spec=ListPageSpec(
            url_template="https://www.zhipin.com/",
            item_selector=".sub-li",
            title_selector="p.name",
            link_selector="a.job-info",
            city_selector=".job-text span",
            company_selector=".sub-li-bottom .name",
        ),
    ),
    # 万行教师人才网 · 教师岗列表（城市与学历/经验同在一个节点，只取首行；
    # 学校名挂在 logo 链接的 title 属性上，没有可见文字）
    "job910": SiteDefinition(
        key="job910",
        label="万行教师人才网",
        spec=ListPageSpec(
            url_template="https://www.job910.com/",
            item_selector=".job-list",
            title_selector=".list-name a",
            link_selector=".list-name a",
            city_selector=".list-child-1",
            company_selector=".job-list-4 a",
        ),
    ),
    # 应届生 · 名企校园招聘项目（列表本身就是项目名，无独立雇主/城市字段 → 留空）
    "yingjiesheng": SiteDefinition(
        key="yingjiesheng",
        label="应届生",
        spec=ListPageSpec(
            url_template="https://www.yingjiesheng.com/",
            item_selector=".enterprise-list-item",
            title_selector=".enterprise-list-title",
            link_kind="campaign",
        ),
    ),
}


def available_sites() -> list[str]:
    """所有已注册站点的 key（字典序）。"""
    return sorted(SITES)


def get_site(key: str) -> SiteDefinition:
    """取一个站点定义；未知 key 抛 `KeyError`（附可用 key）。"""
    try:
        return SITES[key]
    except KeyError:
        raise KeyError(f"未知站点 {key!r}；可用：{', '.join(available_sites())}") from None


def build_site(key: str, *, fetcher: TextFetcher) -> Crawler:
    """按 key 构造一个抓取器。"""
    site = get_site(key)
    return site.crawler_class(
        company=site.label,
        careers_url=site.careers_url,
        spec=site.spec,
        fetcher=fetcher,
    )


def build_all(*, fetcher: TextFetcher, keys: Iterable[str] | None = None) -> list[Crawler]:
    """构造一批抓取器（默认全部注册站点）。"""
    selected = list(keys) if keys is not None else available_sites()
    return [build_site(key, fetcher=fetcher) for key in selected]


__all__ = ["SITES", "SiteDefinition", "available_sites", "build_all", "build_site", "get_site"]
