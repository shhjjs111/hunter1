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
