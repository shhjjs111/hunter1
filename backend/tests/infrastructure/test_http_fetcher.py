"""HTTP 抓取器单元测试 —— 全部离线（httpx.MockTransport + 假时钟）。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

import httpx
import pytest

from hunter1.platform.fetch.http import FetchError, HttpFetcher
from hunter1.platform.fetch.limits import HostLimiter
from tests.conftest import FakeClock


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


def _fetcher(clock: FakeClock, handler, **kwargs) -> HttpFetcher:
    limiter = HostLimiter(
        per_host=2,
        backoff_base=0.1,
        backoff_cap=1.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return HttpFetcher(
        limiter=limiter,
        transport=httpx.MockTransport(handler),
        retries=kwargs.pop("retries", 3),
        **kwargs,
    )


class TestSuccess:
    def test_returns_text_on_200(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>ok</html>")

        fetcher = _fetcher(clock, handler)
        assert fetcher.get_text("https://a.com/x") == "<html>ok</html>"

    def test_sends_user_agent(self, clock: FakeClock) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("user-agent", "")
            return httpx.Response(200, text="ok")

        _fetcher(clock, handler).get_text("https://a.com/x")
        assert "Mozilla" in seen["ua"]  # 默认带浏览器 UA，避免被简单 UA 拦截

    def test_custom_headers_are_forwarded(self, clock: FakeClock) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["x"] = request.headers.get("x-test", "")
            return httpx.Response(200, text="ok")

        _fetcher(clock, handler).get_text("https://a.com/x", headers={"X-Test": "1"})
        assert seen["x"] == "1"


class TestFailures:
    def test_404_is_not_retried(self, clock: FakeClock) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        fetcher = _fetcher(clock, handler)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/x")
        assert excinfo.value.code == "http_404"
        assert calls["n"] == 1  # 客户端错误不重试

    def test_transient_status_is_retried_then_succeeds(self, clock: FakeClock) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503 if calls["n"] < 3 else 200, text="done")

        fetcher = _fetcher(clock, handler, retries=3)
        assert fetcher.get_text("https://a.com/x") == "done"
        assert calls["n"] == 3

    def test_exhausted_retries_raise(self, clock: FakeClock) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503)

        fetcher = _fetcher(clock, handler, retries=2)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/x")
        assert excinfo.value.code == "http_503"
        assert calls["n"] == 2

    def test_network_error_is_retried_then_raises(self, clock: FakeClock) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectError("boom")

        fetcher = _fetcher(clock, handler, retries=2)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/x")
        assert excinfo.value.code == "transport_failed"
        assert calls["n"] == 2


class TestLimiterIntegration:
    def test_transient_failure_records_into_limiter(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        fetcher = _fetcher(clock, handler, retries=1)
        with pytest.raises(FetchError):
            fetcher.get_text("https://a.com/x")
        assert fetcher.limiter.cooldown_remaining("a.com") > 0

    def test_success_leaves_no_cooldown(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        fetcher = _fetcher(clock, handler)
        fetcher.get_text("https://a.com/x")
        assert fetcher.limiter.cooldown_remaining("a.com") == 0.0


class TestFetchErrorShape:
    def test_error_carries_code_and_url(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        fetcher = _fetcher(clock, handler, retries=1)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/secret")
        error = excinfo.value
        assert error.code == "http_403"
        assert error.url == "https://a.com/secret"
        assert "http_403" in str(error)


class TestMalformedUrl:
    def test_malformed_url_fails_fast_with_stable_code(self, clock: FakeClock) -> None:
        """畸形 URL 必须变成 FetchError，且不碰网络、不共享主机桶。

        限流器对解析不出主机的 URL fail-closed（抛 ValueError）；HttpFetcher
        把它翻译成统一的 FetchError —— 「错误一律 FetchError」是这层的承诺，
        不能让 ValueError / httpx.InvalidURL 以别的形状漏出去。
        """

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("畸形 URL 不该到达传输层")

        fetcher = _fetcher(clock, handler, retries=1)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("not a url")
        assert excinfo.value.code == "invalid_url"
