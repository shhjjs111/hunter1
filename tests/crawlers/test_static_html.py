"""静态 HTML 列表页适配器单元测试 —— 离线（内联 HTML fixture）。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

from hunter1.crawlers.static_html import ListPageSpec, StaticHtmlCrawler, parse_list_page

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
