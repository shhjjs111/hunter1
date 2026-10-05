"""抓取守卫的测试 —— 「被拦截」必须是一种**显式错误**，不是空结果。

背景（实测）：BOSS直聘在连续请求后会返回 `<title>安全验证 - BOSS直聘</title>`
的风控页，HTTP 仍是 200。若只看「解析出几条」，会得到 0 条 —— 与
「这个站点今天确实没有新岗位」 indistinguishable。规划 §6.2 明确要求
这类页面识别为「需人工介入」且**不静默失败**。
"""

from __future__ import annotations

import pytest

from hunter1.crawlers.guards import CrawlBlockedError, detect_blocking, ensure_not_blocked
from hunter1.crawlers.static_html import ListPageSpec, StaticHtmlCrawler

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
        assert detect_blocking(BOSS_CHALLENGE) is not None

    def test_detects_cloudflare_challenge(self) -> None:
        assert detect_blocking(CLOUDFLARE_CHALLENGE) is not None

    def test_detects_access_denied(self) -> None:
        assert detect_blocking(ACCESS_DENIED) is not None

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

    def test_genuinely_empty_page_returns_empty(self) -> None:
        """真正没有岗位的空列表页仍然是「空结果」，不该报错。"""
        empty = "<html><head><title>招聘</title></head><body><ul class='jobs'></ul></body></html>"
        assert self._crawler(empty).fetch() == []
