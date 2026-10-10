"""静态列表页适配器 + 风控守卫测试 —— 离线（内联 HTML fixture）。

（从旧 `tests/crawlers/test_static_html.py` 与 `test_guards.py` 合并迁移：
两者测的是同一层的两半 —— 解析与「这不是内容页」的判别，拆开只会让读者多跳一次。）
"""

from __future__ import annotations

import pytest

from hunter1.slices.crawl.adapters import ListPageSpec, StaticHtmlCrawler, parse_list_page
from hunter1.slices.crawl.guards import (
    BLOCK_MARKUP_SIGNALS,
    BLOCK_TITLE_SIGNALS,
    CrawlBlockedError,
    CrawlEmptyPageError,
    detect_blocking,
    ensure_not_blocked,
)

PAGE_1 = """
<html><body>
  <ul class="job-list">
    <li class="job-item">
      <a class="job-title" href="/jobs/1001">AI产品经理（2027校招）</a>
      <span class="job-city">北京</span>
    </li>
    <li class="job-item">
      <a class="job-title" href="https://other.com/jobs/1002">算法产品经理</a>
      <span class="job-city">上海</span>
    </li>
  </ul>
</body></html>
"""

PAGE_2 = """
<html><body>
  <ul class="job-list">
    <li class="job-item">
      <a class="job-title" href="/jobs/1003">数据产品经理</a>
      <span class="job-city">深圳</span>
    </li>
  </ul>
</body></html>
"""

PAGE_EMPTY = "<html><body><ul class='job-list'></ul></body></html>"


def _spec(**overrides: object) -> ListPageSpec:
    base: dict[str, object] = {
        "url_template": "https://a.com/jobs?page={page}",
        "item_selector": "li.job-item",
        "title_selector": "a.job-title",
        "city_selector": "span.job-city",
        "max_pages": 3,
    }
    base.update(overrides)
    return ListPageSpec(**base)  # type: ignore[arg-type]


class FakeFetcher:
    """按 URL 返回预置 HTML；记录请求过的 URL。"""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get_text(self, url: str, **_kwargs: object) -> str:
        self.requested.append(url)
        return self.pages.get(url, PAGE_EMPTY)


class TestParseListPage:
    def test_extracts_items(self) -> None:
        jobs = parse_list_page(
            PAGE_1, _spec(), company="示例科技", page_url="https://a.com/jobs?page=1"
        )
        assert [j.title for j in jobs] == ["AI产品经理（2027校招）", "算法产品经理"]
        assert [j.city for j in jobs] == ["北京", "上海"]

    def test_resolves_relative_links_against_page_url(self) -> None:
        jobs = parse_list_page(
            PAGE_1, _spec(), company="示例科技", page_url="https://a.com/jobs?page=1"
        )
        assert jobs[0].detail_url == "https://a.com/jobs/1001"

    def test_keeps_absolute_links(self) -> None:
        jobs = parse_list_page(
            PAGE_1, _spec(), company="示例科技", page_url="https://a.com/jobs?page=1"
        )
        assert jobs[1].detail_url == "https://other.com/jobs/1002"

    def test_sets_company_and_source(self) -> None:
        jobs = parse_list_page(PAGE_1, _spec(), company="示例科技", page_url="https://a.com/")
        assert all(j.company == "示例科技" for j in jobs)
        assert all(j.source == "示例科技" for j in jobs)

    def test_strips_whitespace_in_title(self) -> None:
        html = '<li class="job-item"><a class="job-title" href="/x">\n   测试岗  \n</a></li>'
        jobs = parse_list_page(html, _spec(), company="C", page_url="https://a.com/")
        assert jobs[0].title == "测试岗"

    def test_item_without_link_is_skipped(self) -> None:
        html = '<li class="job-item"><span class="job-title">无链接岗</span></li>'
        jobs = parse_list_page(html, _spec(), company="C", page_url="https://a.com/")
        assert jobs == []

    def test_item_without_title_is_skipped(self) -> None:
        html = '<li class="job-item"><a class="job-title" href="/x"></a></li>'
        jobs = parse_list_page(html, _spec(), company="C", page_url="https://a.com/")
        assert jobs == []

    def test_empty_page_yields_nothing(self) -> None:
        assert parse_list_page(PAGE_EMPTY, _spec(), company="C", page_url="https://a.com/") == []

    def test_city_is_none_when_selector_absent(self) -> None:
        jobs = parse_list_page(
            PAGE_1, _spec(city_selector=None), company="C", page_url="https://a.com/"
        )
        assert jobs[0].city is None

    def test_duplicate_titles_on_page_are_deduped_by_url(self) -> None:
        html = """
        <li class="job-item"><a class="job-title" href="/jobs/1">同名岗</a></li>
        <li class="job-item"><a class="job-title" href="/jobs/1">同名岗</a></li>
        """
        jobs = parse_list_page(html, _spec(), company="C", page_url="https://a.com/")
        assert len(jobs) == 1

    def test_placeholder_links_do_not_swallow_real_jobs(self) -> None:
        """`href="#"` / `javascript:void(0)` 的条目不能把同页真岗位一起吞掉。

        归一化后它们分别是「列表页自己」与空串 —— 原先校验只看原始 href 非空，
        于是这些条目共享同一个归一化结果：首条被当成一条岗位（链接还指向列表页），
        其余全部按「同页重复」丢掉。真实后果：整页岗位只入库 1 条，而且 fetched=1
        看起来还挺正常。
        """
        html = """
        <ul class="job-list">
          <li class="job-item"><a class="job-title" href="#">回到顶部</a></li>
          <li class="job-item"><a class="job-title" href="javascript:void(0)">甲岗位</a></li>
          <li class="job-item"><a class="job-title" href="/jobs/1001">乙岗位</a></li>
          <li class="job-item"><a class="job-title" href="javascript:void(0)">丙岗位</a></li>
          <li class="job-item"><a class="job-title" href="/jobs/1002">丁岗位</a></li>
          <li class="job-item"><a class="job-title" href="#">回到顶部</a></li>
        </ul>
        """
        jobs = parse_list_page(html, _spec(), company="C", page_url="https://a.com/jobs")

        assert [job.title for job in jobs] == ["乙岗位", "丁岗位"]
        assert [job.detail_url for job in jobs] == [
            "https://a.com/jobs/1001",
            "https://a.com/jobs/1002",
        ]

    def test_hash_routed_links_stay_distinct(self) -> None:
        """hash 路由（`#/job/123`）的整页岗位必须各留一条。

        fragment 就是它们的岗位身份 —— 归一化时丢掉 fragment 会让整页归一成同一个
        地址（并共享同一个 `JobIdentity`），除首条外全被当成重复丢掉。
        """
        html = """
        <ul class="job-list">
          <li class="job-item"><a class="job-title" href="#/job/1">甲岗位</a></li>
          <li class="job-item"><a class="job-title" href="#/job/2">乙岗位</a></li>
          <li class="job-item"><a class="job-title" href="#/job/3">丙岗位</a></li>
        </ul>
        """
        jobs = parse_list_page(html, _spec(), company="C", page_url="https://a.com/jobs")

        assert [job.title for job in jobs] == ["甲岗位", "乙岗位", "丙岗位"]
        assert [job.detail_url for job in jobs] == [
            "https://a.com/jobs#/job/1",
            "https://a.com/jobs#/job/2",
            "https://a.com/jobs#/job/3",
        ]


class TestFetch:
    def test_single_page(self) -> None:
        page1 = "https://a.com/jobs?page=1"
        fetcher = FakeFetcher({page1: PAGE_1})
        crawler = StaticHtmlCrawler(
            company="示例科技",
            careers_url="https://a.com/jobs",
            spec=_spec(max_pages=1),
            fetcher=fetcher,
        )
        jobs = crawler.fetch()
        assert [j.title for j in jobs] == ["AI产品经理（2027校招）", "算法产品经理"]
        assert fetcher.requested == [page1]

    def test_paginates_until_empty(self) -> None:
        pages = {
            "https://a.com/jobs?page=1": PAGE_1,
            "https://a.com/jobs?page=2": PAGE_2,
            "https://a.com/jobs?page=3": PAGE_EMPTY,
        }
        fetcher = FakeFetcher(pages)
        crawler = StaticHtmlCrawler(
            company="示例科技",
            careers_url="https://a.com/jobs",
            spec=_spec(max_pages=5),
            fetcher=fetcher,
        )
        jobs = crawler.fetch()
        assert [j.title for j in jobs] == ["AI产品经理（2027校招）", "算法产品经理", "数据产品经理"]
        # 第 3 页为空 → 停止，不继续请求第 4、5 页
        assert fetcher.requested == [
            "https://a.com/jobs?page=1",
            "https://a.com/jobs?page=2",
            "https://a.com/jobs?page=3",
        ]

    def test_respects_max_pages(self) -> None:
        fetcher = FakeFetcher(
            {"https://a.com/jobs?page=1": PAGE_1, "https://a.com/jobs?page=2": PAGE_2}
        )
        crawler = StaticHtmlCrawler(
            company="C",
            careers_url="https://a.com/jobs",
            spec=_spec(max_pages=2),
            fetcher=fetcher,
        )
        crawler.fetch()
        assert len(fetcher.requested) == 2

    def test_dedupes_across_pages(self) -> None:
        """同一岗位出现在两页时只保留一条。"""
        fetcher = FakeFetcher(
            {"https://a.com/jobs?page=1": PAGE_1, "https://a.com/jobs?page=2": PAGE_1}
        )
        crawler = StaticHtmlCrawler(
            company="C",
            careers_url="https://a.com/jobs",
            spec=_spec(max_pages=2),
            fetcher=fetcher,
        )
        jobs = crawler.fetch()
        assert len(jobs) == 2

    def test_url_template_without_page_placeholder_is_single_page(self) -> None:
        fetcher = FakeFetcher({"https://a.com/jobs": PAGE_1})
        crawler = StaticHtmlCrawler(
            company="C",
            careers_url="https://a.com/jobs",
            spec=_spec(url_template="https://a.com/jobs", max_pages=9),
            fetcher=fetcher,
        )
        jobs = crawler.fetch()
        assert len(jobs) == 2
        assert fetcher.requested == ["https://a.com/jobs"]  # 不会重复请求同一页


BOARD_PAGE = """
<ul class="hb">
  <li class="row">
    <a class="t" href="/j/1">产品经理</a>
    <span class="c">上海</span>
    <span class="co">甲公司</span>
  </li>
  <li class="row">
    <a class="t" href="/j/2">算法工程师</a>
    <span class="c">北京</span>
    <span class="co">乙公司</span>
  </li>
</ul>
"""


def _board_spec(**overrides: object) -> ListPageSpec:
    """岗位聚合站的规格：公司逐条不同。"""
    base: dict[str, object] = {
        "url_template": "https://b.com/list",
        "item_selector": "li.row",
        "title_selector": "a.t",
        "link_selector": "a.t",
        "city_selector": "span.c",
        "company_selector": "span.co",
    }
    base.update(overrides)
    return ListPageSpec(**base)  # type: ignore[arg-type]


class TestJobBoardSpec:
    """聚合站（一页多个公司）的解析约定。"""

    def test_per_item_company_overrides_default(self) -> None:
        jobs = parse_list_page(
            BOARD_PAGE, _board_spec(), company="招聘平台", page_url="https://b.com/list"
        )
        assert [j.company for j in jobs] == ["甲公司", "乙公司"]

    def test_company_falls_back_to_default_when_selector_misses(self) -> None:
        jobs = parse_list_page(
            BOARD_PAGE,
            _board_spec(company_selector="span.nope"),
            company="招聘平台",
            page_url="https://b.com/list",
        )
        assert [j.company for j in jobs] == ["招聘平台", "招聘平台"]

    def test_no_company_selector_keeps_default(self) -> None:
        jobs = parse_list_page(
            BOARD_PAGE,
            _board_spec(company_selector=None),
            company="P",
            page_url="https://b.com/list",
        )
        assert all(j.company == "P" for j in jobs)

    def test_job_type_and_link_kind_propagate(self) -> None:
        jobs = parse_list_page(
            BOARD_PAGE,
            _board_spec(job_type="实习", link_kind="campaign"),
            company="P",
            page_url="https://b.com/list",
        )
        assert all(j.job_type == "实习" for j in jobs)
        assert all(j.link_kind == "campaign" for j in jobs)

    def test_defaults_are_campus_and_detail(self) -> None:
        jobs = parse_list_page(
            BOARD_PAGE, _board_spec(), company="P", page_url="https://b.com/list"
        )
        assert all(j.job_type == "校招" for j in jobs)
        assert all(j.link_kind == "detail" for j in jobs)

    def test_item_itself_is_the_link(self) -> None:
        """条目节点本身就是 <a>（如猎聘卡片）——不能只找后代。"""
        html = '<a class="card" href="/j/9"><span class="t">数据分析</span></a>'
        spec = ListPageSpec(
            url_template="https://c.com/l", item_selector="a.card", title_selector="span.t"
        )
        jobs = parse_list_page(html, spec, company="C", page_url="https://c.com/l")
        assert [j.detail_url for j in jobs] == ["https://c.com/j/9"]

    def test_company_is_stripped(self) -> None:
        html = (
            '<li class="row"><a class="t" href="/j/1">岗</a>'
            '<span class="co">\n  某公司  \n</span></li>'
        )
        jobs = parse_list_page(html, _board_spec(), company="P", page_url="https://b.com/list")
        assert jobs[0].company == "某公司"


class TestFieldExtractionFallbacks:
    """真实页面里「一个语义字段」常有多种写法，抽取要按固定优先级来。"""

    def test_multi_line_field_takes_first_non_empty_line(self) -> None:
        """城市节点里常连带着学历/经验 —— 只取第一行，不把整块拼成城市。"""
        html = (
            '<li class="row"><a class="t" href="/j/1">教师</a>'
            '<span class="c">\n  武汉-洪山区\n  <span></span>\n  本科\n</span></li>'
        )
        jobs = parse_list_page(html, _board_spec(), company="P", page_url="https://b.com/list")
        assert jobs[0].city == "武汉-洪山区"

    def test_field_falls_back_to_title_attribute(self) -> None:
        """节点里只有图片、文字为空时，用 title 属性（如学校名）。"""
        html = (
            '<li class="row"><a class="t" href="/j/1">教师</a>'
            '<span class="co"><a title="某实验学校"><img src="x.png"/></a></span></li>'
        )
        jobs = parse_list_page(html, _board_spec(), company="P", page_url="https://b.com/list")
        assert jobs[0].company == "某实验学校"

    def test_field_falls_back_to_image_alt(self) -> None:
        html = (
            '<li class="row"><a class="t" href="/j/1">教师</a>'
            '<span class="co"><a><img alt="另一所学校" src="x.png"/></a></span></li>'
        )
        jobs = parse_list_page(html, _board_spec(), company="P", page_url="https://b.com/list")
        assert jobs[0].company == "另一所学校"

    def test_text_wins_over_title_attribute(self) -> None:
        html = (
            '<li class="row"><a class="t" href="/j/1">教师</a>'
            '<span class="co"><a title="标题属性">可见文字</a></span></li>'
        )
        jobs = parse_list_page(html, _board_spec(), company="P", page_url="https://b.com/list")
        assert jobs[0].company == "可见文字"


# ---- 风控守卫（原 test_guards.py）----

BOSS_CHALLENGE = (
    '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">'
    '<title id="pageTitle">安全验证 - BOSS直聘</title></head>'
    '<body><div class="verify-row-code">请完成安全验证</div></body></html>'
)

CLOUDFLARE_CHALLENGE = (
    "<html><head><title>Just a moment...</title></head>"
    '<body><div id="challenge-platform"></div></body></html>'
)

ACCESS_DENIED = "<html><head><title>Access Denied</title></head><body>denied</body></html>"

NORMAL_PAGE = (
    "<html><head><title>招聘 - 示例站点</title></head>"
    '<body><ul class="jobs"><li class="job">'
    '<a class="t" href="/j/1">安全验证工程师</a><span class="c">北京</span>'
    "</li></ul></body></html>"
)


class TestDetectBlocking:
    def test_detects_boss_challenge(self) -> None:
        # 断言**具体信号名**（不是「检出了就行」）：原先只验 `is not None`，
        # 把词表里任意一个词换掉仍绿 —— 检出的是哪个信号从没被钉住。
        assert detect_blocking(BOSS_CHALLENGE) == "安全验证"

    def test_detects_cloudflare_challenge(self) -> None:
        # 该页标题与结构标记都命中 —— 断言标题信号，顺带钉住「标题优先于结构标记」。
        assert detect_blocking(CLOUDFLARE_CHALLENGE) == "just a moment"

    def test_detects_access_denied(self) -> None:
        assert detect_blocking(ACCESS_DENIED) == "access denied"

    def test_signal_tables_cover_the_known_vendors(self) -> None:
        """词表不能被**删空**，也不能被**删项** —— 下面那条参数化用例只覆盖表里剩下的，
        少一个信号它照样全绿（线上表现为「抓到 0 条」而不是报错）。

        用 `>=` 超集断言：**加**信号不打扰（新型挑战页随时会冒出来），
        **删/改**任何一个已覆盖的信号立刻变红。
        """
        assert set(BLOCK_MARKUP_SIGNALS) >= {
            "challenge-platform",
            "cf-challenge",
            "cf_chl_opt",
            "geetest",
            "/verify-slider",
            "grecaptcha",
            "hcaptcha",
        }
        assert set(BLOCK_TITLE_SIGNALS) >= {
            "安全验证",
            "人机验证",
            "异常访问",
            "访问受限",
            "访问过于频繁",
            "just a moment",
            "attention required",
            "access denied",
            "checking your browser",
            "enable javascript and cookies",
        }

    @pytest.mark.parametrize("signal", BLOCK_MARKUP_SIGNALS)
    def test_every_markup_signal_is_detected(self, signal: str) -> None:
        """**每一个**结构标记都要能单独命中 —— 这条通路此前零正向用例。

        实测：把 `BLOCK_MARKUP_SIGNALS` 整个删空，测试全绿 —— 而 7 个验证码厂商
        信号（geetest / grecaptcha / hcaptcha / cf_chl_opt…）全失效。后果正是
        guards.py docstring 要避免的那种失败模式：线上换挑战页时**静默漏检**，
        表现为「抓到 0 条」而不是报错。

        标题刻意用一个正常标题：只可能由结构标记命中。
        """
        html = (
            "<html><head><title>招聘 - 示例站点</title></head>"
            f'<body><div data-x="{signal}"></div></body></html>'
        )
        assert detect_blocking(html) == signal

    @pytest.mark.parametrize("signal", BLOCK_TITLE_SIGNALS)
    def test_every_title_signal_is_detected(self, signal: str) -> None:
        """同理：词表里的每个标题信号都要能命中（改词/删词即红）。"""
        html = f"<html><head><title>{signal} - 示例站点</title></head><body></body></html>"
        assert detect_blocking(html) == signal

    def test_normal_page_is_not_flagged(self) -> None:
        assert detect_blocking(NORMAL_PAGE) is None

    def test_job_title_mentioning_verification_is_not_flagged(self) -> None:
        """岗位标题里出现「安全验证」不是拦截 —— 只看标题，不看正文。"""
        html = (
            "<html><head><title>安全验证工程师招聘 - 示例</title></head>"
            '<body><ul class="jobs"><li class="job">'
            '<a class="t" href="/j/1">安全验证工程师</a><span class="c">北京</span>'
            "</li></ul></body></html>"
        )
        assert detect_blocking(html) is None

    def test_empty_html_is_not_flagged(self) -> None:
        assert detect_blocking("") is None


class TestEnsureNotBlocked:
    def test_raises_with_url_and_signal(self) -> None:
        with pytest.raises(CrawlBlockedError) as excinfo:
            ensure_not_blocked(BOSS_CHALLENGE, url="https://www.zhipin.com/")
        assert "https://www.zhipin.com/" in str(excinfo.value)
        assert excinfo.value.url == "https://www.zhipin.com/"

    def test_passes_through_normal_page(self) -> None:
        ensure_not_blocked(NORMAL_PAGE, url="https://example.com/")


class TestCrawlerRaisesOnChallenge:
    """被拦截时抓取器要抛错，而不是返回空列表。"""

    class _Fetcher:
        def __init__(self, html: str) -> None:
            self.html = html

        def get_text(self, url: str, **_kw: object) -> str:
            return self.html

    def _crawler(self, html: str) -> StaticHtmlCrawler:
        return StaticHtmlCrawler(
            company="某站",
            careers_url="https://site.com/jobs",
            spec=ListPageSpec(
                url_template="https://site.com/jobs",
                item_selector="li.job",
                title_selector="a.t",
                city_selector="span.c",
            ),
            fetcher=self._Fetcher(html),
        )

    def test_challenge_page_raises(self) -> None:
        with pytest.raises(CrawlBlockedError):
            self._crawler(BOSS_CHALLENGE).fetch()

    def test_empty_list_page_reports_instead_of_succeeding_silently(self) -> None:
        """**决策变更**：首页一条都没解析出来时不再返回空结果，而是报错。

        原先这条断言「真正没有岗位的空列表页仍然是空结果，不该报错」。但页面上的
        空 `<ul class='jobs'>` 与「选择器失效 / 站点改版」在 HTML 层面**无法区分**
        （两种情况的 `select('li.job')` 都是空集）—— 于是「今天真没岗位」和「我们
        读不懂这个页面了」在界面上一模一样：都显示「抓取成功，0 条」。

        审查决定选前者：宁可让运维看一眼（错误文案里两种可能都写了、并请人工确认），
        也不要让「读不懂页面」永远沉默 —— 后者用户永远不会知道。
        """
        empty = "<html><head><title>招聘</title></head><body><ul class='jobs'></ul></body></html>"
        with pytest.raises(CrawlEmptyPageError) as excinfo:
            self._crawler(empty).fetch()
        assert "empty_first_page" in str(excinfo.value)
        assert "人工确认" in str(excinfo.value)
