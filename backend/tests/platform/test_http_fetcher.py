"""HTTP 抓取器单元测试 —— 全部离线（httpx.MockTransport + 假时钟）。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
"""

from __future__ import annotations

import httpx
import pytest

from hunter1.platform.fetch.http import FetchError, HttpFetcher
from hunter1.platform.fetch.limits import HostLimiter, ResourceLimitTimeoutError
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


class TestEncodingFallback:
    """编码回退（M5）：响应头不带 charset 的中文页不能静默变 U+FFFD。

    修复前 httpx 在无 charset、无探测器时固定按 utf-8 + replace 解码，
    GBK 页面整页变替换字符且不抛错（乱码不是异常，正是「静默失败」）。
    """

    def test_gbk_body_without_charset_decodes(self, clock: FakeClock) -> None:
        payload = "<html><title>中文岗位</title></html>".encode("gbk")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload, headers={"Content-Type": "text/html"})

        text = _fetcher(clock, handler).get_text("https://a.com/x")
        assert "中文岗位" in text
        assert "\ufffd" not in text

    def test_meta_charset_decodes_when_header_silent(self, clock: FakeClock) -> None:
        """编码只写在 `<meta>` 里。

        载荷刻意用 **cp1251**（俄文）：它同时是「utf-8 严格解码失败」且
        「gb18030 能解出乱码」的字节 —— 于是只有真的读了 `<meta>` 才能得到
        「Привет」。原先用 GBK 载荷，gb18030 兜底也能过：删掉 meta 分支照样全绿，
        测试名承诺的优先级根本没被钉住。
        """
        payload = (
            '<html><head><meta charset="windows-1251"></head><body>Привет</body></html>'
        ).encode("cp1251")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload, headers={"Content-Type": "text/html"})

        text = _fetcher(clock, handler).get_text("https://a.com/x")
        assert "Привет" in text
        assert "\ufffd" not in text

    def test_utf8_body_without_charset_still_decodes(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content="中文".encode(), headers={"Content-Type": "text/html"}
            )

        assert _fetcher(clock, handler).get_text("https://a.com/x") == "中文"

    def test_declared_charset_from_header_wins(self, clock: FakeClock) -> None:
        """响应头的 charset 优先级最高（同样是 cp1251 载荷：只有照声明解才对）。"""
        payload = "Привет".encode("cp1251")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/html; charset=windows-1251"},
            )

        assert _fetcher(clock, handler).get_text("https://a.com/x") == "Привет"

    def test_header_charset_beats_meta_charset(self, clock: FakeClock) -> None:
        """两个声明都在时，响应头优先于 `<meta>`（HTTP 层的声明更权威）。

        载荷用 cp1251，头部说 windows-1251、meta 谎称 iso-8859-1 —— 信 meta 就会
        得到一串还能看、但全错的西文（iso-8859-1 解任何字节都不报错，正好把
        「谁优先」钉死：换序即变乱码）。
        """
        payload = (
            '<html><head><meta charset="iso-8859-1"></head><body>Привет</body></html>'
        ).encode("cp1251")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/html; charset=windows-1251"},
            )

        text = _fetcher(clock, handler).get_text("https://a.com/x")
        assert "Привет" in text

    def test_lying_latin1_header_with_utf8_body_decodes_correctly(self, clock: FakeClock) -> None:
        """站点谎报 `ISO-8859-1`、实际发 UTF-8 —— 不能静默变乱码。

        HTTP 的默认字符集就是 ISO-8859-1，而「默认头 + UTF-8 体」是中文站极常见的
        形态。latin-1 解任何字节都不抛异常 —— 先采信它就把 mojibake 当成功，
        后面整条回退链一步都走不到（实测：得到 `ä¸­æ\\x96\\x87…` 而不是 `中文测试`）。
        """
        payload = "中文测试".encode()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/html; charset=iso-8859-1"},
            )

        text = _fetcher(clock, handler).get_text("https://a.com/x")
        assert text == "中文测试"
        assert "\ufffd" not in text

    def test_genuine_latin1_body_still_decodes(self, clock: FakeClock) -> None:
        """真正用 latin-1 的页面不能被「utf-8 优先」误伤（它排在 utf-8 之后）。"""
        payload = "café au lait".encode("latin-1")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/html; charset=iso-8859-1"},
            )

        assert _fetcher(clock, handler).get_text("https://a.com/x") == "café au lait"

    def test_lying_latin1_header_with_gbk_body_decodes_correctly(self, clock: FakeClock) -> None:
        """站点谎报 `ISO-8859-1`、实际发 GBK —— 中文回退不能被「不可信的声明」挡死。

        原先的候选顺序是 `[声明的不可信编码, gb18030, big5]`：latin-1 能解码任意字节
        序列、永不抛异常，于是**必然**在第一档 return —— gb18030/big5 成了死代码，
        页面静默变 mojibake（无异常、无日志），与本模块「不静默失败」的承诺冲突。
        """
        payload = "<html><title>中文岗位</title></html>".encode("gbk")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/html; charset=iso-8859-1"},
            )

        text = _fetcher(clock, handler).get_text("https://a.com/x")
        assert "中文岗位" in text
        assert "\ufffd" not in text


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


class TestResponseSizeCap:
    """单响应体积上限 —— 限流器管并发，管不到「一个响应有多大」。

    重定向落到大文件（或站点返了个巨型日志页）时，无上限的读取会把内存吃满。
    超限按 `too_large` 报错，**不静默截断**：半截 HTML 会产出看起来正常、实际
    缺了一半的岗位列表。
    """

    def test_oversized_body_is_rejected(self, clock: FakeClock) -> None:
        payload = b"x" * 5000

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload)

        fetcher = _fetcher(clock, handler, retries=1, max_bytes=1024)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/big")
        assert excinfo.value.code == "too_large"
        assert excinfo.value.url == "https://a.com/big"

    def test_declared_length_over_cap_is_rejected_without_reading(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"tiny", headers={"Content-Length": "100000000"})

        fetcher = _fetcher(clock, handler, retries=1, max_bytes=1024)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/big")
        assert excinfo.value.code == "too_large"

    def test_body_within_cap_still_works(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>ok</html>")

        fetcher = _fetcher(clock, handler, max_bytes=1024)
        assert fetcher.get_text("https://a.com/x") == "<html>ok</html>"


class TestRobots:
    """遵守 robots.txt —— 默认**关**（生产装配在组装根打开）。

    「站点明确写了 don't crawl」与「站点什么都没写」此前的处理完全一样：都抓。
    """

    def _fetcher_with_robots(self, clock: FakeClock, handler, **kwargs) -> HttpFetcher:
        return _fetcher(clock, handler, respect_robots=True, retries=1, **kwargs)

    def test_disallowed_path_is_not_fetched(self, clock: FakeClock) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /private")
            return httpx.Response(200, text="<html>ok</html>")

        fetcher = self._fetcher_with_robots(clock, handler)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/private/job1")

        assert excinfo.value.code == "robots_disallowed"
        assert calls == ["https://a.com/robots.txt"], "被禁止的路径不该真的发出请求"

    def test_allowed_path_is_fetched_and_robots_is_cached(self, clock: FakeClock) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /private")
            return httpx.Response(200, text="<html>ok</html>")

        fetcher = self._fetcher_with_robots(clock, handler)
        assert fetcher.get_text("https://a.com/jobs") == "<html>ok</html>"
        assert fetcher.get_text("https://a.com/jobs2") == "<html>ok</html>"
        assert calls.count("https://a.com/robots.txt") == 1, "robots.txt 每个主机只该取一次"

    def test_missing_robots_means_no_restriction(self, clock: FakeClock) -> None:
        """404 的 robots.txt 是常态（等于「没声明限制」），不该拦下抓取。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, text="<html>ok</html>")

        fetcher = self._fetcher_with_robots(clock, handler)
        assert fetcher.get_text("https://a.com/jobs") == "<html>ok</html>"

    def test_unreachable_robots_is_reported_but_not_blocking(
        self, clock: FakeClock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """读不到 robots.txt 时按「未声明限制」处理，但要**留痕**。

        把它静默当成「没限制」是方向相反的静默失败：用户永远不知道自己的抓取
        其实没有遵守任何声明。
        """

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                raise httpx.ConnectError("boom")
            return httpx.Response(200, text="<html>ok</html>")

        fetcher = self._fetcher_with_robots(clock, handler)
        assert fetcher.get_text("https://a.com/jobs") == "<html>ok</html>"
        assert "robots.txt" in capsys.readouterr().err

    def test_robots_is_off_by_default(self, clock: FakeClock) -> None:
        """默认不发 robots.txt 请求 —— 免得给每个用例多打一次网络。"""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, text="<html>ok</html>")

        assert _fetcher(clock, handler).get_text("https://a.com/private") == "<html>ok</html>"
        assert calls == ["https://a.com/private"]


class TestRequestIntervalWiring:
    def test_min_interval_reaches_the_default_limiter(self) -> None:
        """不注入 limiter 时，`min_interval` 要落到自建的那个上。"""
        fetcher = HttpFetcher(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok")),
            min_interval=2.0,
        )
        try:
            assert fetcher.limiter.min_interval == 2.0
        finally:
            fetcher.close()

    def test_injected_limiter_keeps_its_own_interval(self, clock: FakeClock) -> None:
        """注入 limiter 时以注入的为准（测试/脚本自己掌控节奏）。"""
        limiter = HostLimiter(min_interval=0.0, monotonic=clock.monotonic, sleep=clock.sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        fetcher = HttpFetcher(
            limiter=limiter,
            transport=httpx.MockTransport(handler),
            min_interval=5.0,
        )
        try:
            assert fetcher.limiter is limiter
        finally:
            fetcher.close()


class TestResourceTimeout:
    """限流器等待超时（`resource_timeout`）—— 这条分支原先在测试里 0 命中。

    真实路径是 `HostLimiter` 的等待超过 timeout（由 limits 自己的测试覆盖）；
    这里用一个永远「占满」的替身，把 HttpFetcher 这一侧的**重试与最终报错**钉住。
    """

    def test_waiting_out_the_limiter_retries_then_reports(self, clock: FakeClock) -> None:
        class Saturated(HostLimiter):
            acquires = 0
            records = 0

            def acquire(self, url: str, *, timeout: float = 30.0):
                Saturated.acquires += 1
                raise ResourceLimitTimeoutError(f"saturated: {url}")

            def record(self, url: str, *, status: int | None = None, **kw: object) -> None:
                Saturated.records += 1

        limiter = Saturated(monotonic=clock.monotonic, sleep=clock.sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("限流器没放行就不该碰网络")

        fetcher = HttpFetcher(limiter=limiter, transport=httpx.MockTransport(handler), retries=3)
        try:
            with pytest.raises(FetchError) as excinfo:
                fetcher.get_text("https://a.com/x")
        finally:
            fetcher.close()

        assert excinfo.value.code == "resource_timeout"
        assert Saturated.acquires == 3  # 每次都重试，直到 retries 用尽
        # 本地限流器排不上队**不是**站点的错：不该记进主机的失败统计
        # （记了会触发冷却，把「本机太忙」变成「这个站点不可用」）。
        assert Saturated.records == 0
