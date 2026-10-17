"""HTTP 抓取器单元测试 —— 全部离线（httpx.MockTransport + 假时钟）。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
"""

from __future__ import annotations

import gzip
import socket
import zlib

import httpx
import pytest

from hunter1.platform.fetch.http import FetchError, HttpFetcher
from hunter1.platform.fetch.limits import HostLimiter, ResourceLimitTimeoutError
from hunter1.platform.fetch.robots import ROBOTS_MAX_BYTES
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

    def test_robots_uses_the_same_host_pinned_user_agent(self, clock: FakeClock) -> None:
        """robots 组选择与页面请求必须看到同一个 UA。"""
        seen: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.path] = request.headers.get("user-agent")
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            return httpx.Response(200, text="ok")

        fetcher = self._fetcher_with_robots(clock, handler)
        assert fetcher.get_text("https://a.com/jobs") == "ok"
        assert seen["/robots.txt"] == seen["/jobs"]
        assert seen["/robots.txt"] != "python-httpx/0.28.1"

    def test_robots_and_page_use_the_private_network_guard(self, clock: FakeClock) -> None:
        """生产装配同时开启 robots 与私网拦截时，两条请求都要走 IP 固定层。"""
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.host or "", request.headers["host"]))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            return httpx.Response(200, text="ok")

        fetcher = _fetcher(
            clock,
            handler,
            respect_robots=True,
            retries=1,
            block_private_hosts=True,
            resolver=lambda _host: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
            ],
        )
        assert fetcher.get_text("https://a.com/jobs") == "ok"
        assert seen == [
            ("93.184.216.34", "a.com"),
            ("93.184.216.34", "a.com"),
        ]

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

    def test_oversized_robots_is_capped_and_not_truncated(self, clock: FakeClock) -> None:
        """超大的 /robots.txt：上限必须在**读的时候**生效，且不静默截断。

        旧实现用 `self._client.get(url)` —— 它把整个响应体先缓冲进内存，随后的
        `content[:ROBOTS_MAX_BYTES]` 只是切片，挡不住一个几百 MB 的 /robots.txt
        （生产装配开了 `respect_robots`，每遇到一个新主机都会打这个文件）。

        第二个取舍也钉在这里：超限按「拿不到 robots」处理（与 404 同类），
        **不**拿前半截去解析 —— 半截 robots 可能正好丢掉那条 `Disallow`，
        于是我们照爬不误，而界面一切正常（同 `_read_capped` 对 HTML 的取舍）。
        """
        # 头部是**有效规则**，后面塞满足够撑过上限的填充：截断与不截断的差别
        # 正好落在「那条 Disallow 还算不算数」上。
        filler = b"# filler\n" * (ROBOTS_MAX_BYTES // 9 + 100)
        robots = b"User-agent: *\nDisallow: /blocked\n" + filler
        assert len(robots) > ROBOTS_MAX_BYTES

        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, content=robots)
            return httpx.Response(200, text="<html>ok</html>")

        fetcher = self._fetcher_with_robots(clock, handler)
        assert fetcher.get_text("https://a.com/blocked") == "<html>ok</html>"

        assert "https://a.com/blocked" in calls, (
            "超限的 robots.txt 被当成有效规则解析了（前半截里那条 Disallow 生效）"
            "—— 应当按「拿不到 robots」处理，而不是拿半截去当真规则"
        )

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


class TestRedirects:
    """重定向治理（M1）：逐跳跟随，且每一跳都重新过 robots 与限速。

    修复前 `httpx.Client(follow_redirects=True)` 在**传输层**静默跟随，而 robots
    判定与限速配额都只按**原始** URL 做一次 —— 跨主机重定向时，目标主机的 robots
    从未被读过、也不占它的限速配额。「对站点礼貌」的定位因此有个后门。
    """

    def test_follows_redirect_to_final(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(302, headers={"Location": "https://a.com/new"})
            return httpx.Response(200, text="<html>final</html>")

        assert _fetcher(clock, handler).get_text("https://a.com/old") == "<html>final</html>"

    def test_relative_location_is_resolved(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/a":
                return httpx.Response(302, headers={"Location": "/b"})
            return httpx.Response(200, text="B")

        assert _fetcher(clock, handler).get_text("https://a.com/a") == "B"

    def test_cross_host_redirect_rechecks_target_robots(self, clock: FakeClock) -> None:
        """跨主机重定向 → **目标**主机的 robots 必须被读到（而不是当作没限制）。"""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/robots.txt":
                if request.url.host == "b.com":
                    return httpx.Response(200, text="User-agent: *\nDisallow: /")
                return httpx.Response(404)  # a.com 未声明限制
            if request.url.host == "a.com":
                return httpx.Response(302, headers={"Location": "https://b.com/secret"})
            return httpx.Response(200, text="<html>b</html>")

        fetcher = _fetcher(clock, handler, respect_robots=True, retries=1)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/start")

        assert excinfo.value.code == "robots_disallowed"
        assert excinfo.value.url == "https://b.com/secret", "被拦的应是**目标** URL"
        assert "https://b.com/robots.txt" in calls, "目标主机的 robots 没被读 —— 又绕开了"
        assert "https://b.com/secret" not in calls, "被目标主机禁止的路径不该真的发出请求"

    def test_cross_host_redirect_charges_target_limiter(self, clock: FakeClock) -> None:
        """跨主机重定向 → 目标主机的失败要记进**它自己**的冷却桶。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "a.com":
                return httpx.Response(302, headers={"Location": "https://b.com/x"})
            return httpx.Response(503)

        fetcher = _fetcher(clock, handler, retries=1)
        with pytest.raises(FetchError):
            fetcher.get_text("https://a.com/start")

        assert fetcher.limiter.cooldown_remaining("b.com") > 0
        assert fetcher.limiter.cooldown_remaining("a.com") == 0.0, "不该把源主机的桶也拖下水"

    def test_redirect_loop_fails_fast(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "https://a.com/loop"})

        fetcher = _fetcher(clock, handler, retries=1)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/loop")
        assert excinfo.value.code == "too_many_redirects"

    def test_non_http_redirect_is_rejected(self, clock: FakeClock) -> None:
        """Location 指向 `file://` 之类 → 拒绝，别把抓取器引导去读本地文件。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "file:///etc/passwd"})

        fetcher = _fetcher(clock, handler, retries=1)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/x")
        assert excinfo.value.code == "invalid_redirect"

    def test_redirect_to_private_address_is_rejected(self, clock: FakeClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("host") == "a.com":
                return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
            return httpx.Response(200, text="secret")

        fetcher = _fetcher(
            clock,
            handler,
            block_private_hosts=True,
            resolver=lambda _host: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
            ],
        )
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/jobs")
        assert excinfo.value.code == "private_address_blocked"


class TestPrivateNetworkGuard:
    def test_literal_loopback_is_rejected_without_network_io(self, clock: FakeClock) -> None:
        called = False

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, text="secret")

        fetcher = _fetcher(clock, handler, block_private_hosts=True)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("http://127.0.0.1:8080/admin")
        assert excinfo.value.code == "private_address_blocked"
        assert called is False

    def test_hostname_resolving_to_private_address_is_rejected(self, clock: FakeClock) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="secret")

        fetcher = _fetcher(
            clock,
            handler,
            block_private_hosts=True,
            resolver=lambda _host: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0))],
        )
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://internal.example/jobs")
        assert excinfo.value.code == "private_address_blocked"

    def test_request_connects_to_the_validated_ip_with_original_host(
        self, clock: FakeClock
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text="ok")

        fetcher = _fetcher(
            clock,
            handler,
            block_private_hosts=True,
            resolver=lambda _host: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
            ],
        )
        assert fetcher.get_text("https://public.example/jobs") == "ok"
        assert seen[0].url.host == "93.184.216.34"
        assert seen[0].headers["host"] == "public.example"
        assert seen[0].extensions["sni_hostname"] == "public.example"

    def test_connection_does_not_resolve_the_hostname_a_second_time(self, clock: FakeClock) -> None:
        answers = iter(
            [
                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.9", 0))],
            ]
        )
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, text="ok")

        fetcher = _fetcher(
            clock,
            handler,
            block_private_hosts=True,
            resolver=lambda _host: next(answers),
        )
        assert fetcher.get_text("https://rebind.example/jobs") == "ok"
        assert len(calls) == 1
        assert calls[0].url.host == "93.184.216.34"

    def test_retries_fail_over_across_validated_public_addresses(self, clock: FakeClock) -> None:
        """首个 A 记录连不上时，下一次尝试应换用另一个已校验的公网地址。"""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host or ""
            seen.append(host)
            if host == "93.184.216.34":
                raise httpx.ConnectError("first address is down")
            return httpx.Response(200, text="ok")

        fetcher = _fetcher(
            clock,
            handler,
            retries=2,
            block_private_hosts=True,
            resolver=lambda _host: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 0)),
            ],
        )
        assert fetcher.get_text("https://multi.example/jobs") == "ok"
        assert seen == ["93.184.216.34", "93.184.216.35"]

    def test_cookie_jar_keeps_original_host_isolation(self, clock: FakeClock) -> None:
        """连接地址换成 IP 后，Cookie 仍必须按原始域名隔离并正常回传。"""
        seen: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.headers["host"]
            seen.append((host, request.headers.get("cookie")))
            if host == "a.example" and request.url.path == "/start":
                return httpx.Response(
                    200,
                    text="a",
                    headers={"Set-Cookie": "sid=A; Domain=a.example; Path=/"},
                )
            return httpx.Response(200, text="ok")

        fetcher = _fetcher(
            clock,
            handler,
            block_private_hosts=True,
            resolver=lambda _host: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
            ],
        )
        assert fetcher.get_text("https://a.example/start") == "a"
        assert fetcher.get_text("https://a.example/next") == "ok"
        assert fetcher.get_text("https://b.example/next") == "ok"
        assert seen == [
            ("a.example", None),
            ("a.example", "sid=A"),
            ("b.example", None),
        ]


class TestUserAgentStability:
    """同主机固定 UA：UA 决定 robots 的组选择，漂移会让同一份 robots 选出不同规则组。"""

    def test_same_host_keeps_one_user_agent(self, clock: FakeClock) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("user-agent", ""))
            return httpx.Response(200, text="ok")

        fetcher = _fetcher(clock, handler)
        # 多打几次：旧实现每请求重选 UA，随机三次「恰好全同」的概率是 1/9 ——
        # 次数太少会让这条回归用例偶发假绿。8 次把偶然通过压到 (1/3)^7。
        for index in range(8):
            fetcher.get_text(f"https://a.com/{index}")

        assert len(set(seen)) == 1, f"同主机 UA 漂移了：{seen}"
        assert seen[0], "UA 不该为空"

    def test_cross_host_redirect_uses_the_target_hosts_own_agent(
        self, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """跨主机重定向时，目标主机拿到的 UA 必须是**它自己**那一个。

        原先 UA 按**起点**主机选、逐跳复用：目标主机于是拿到别的站点的 UA，而它
        下次被直接访问时又是另一个 —— 「同主机固定」只在单跳场景成立。用确定性的
        `random.choice` 让两台主机必然分到不同的 UA，免得靠 1/3 的运气判绿。
        """
        import hunter1.platform.fetch.http as http_module

        agents = iter(["UA-FIRST", "UA-SECOND", "UA-FIRST", "UA-SECOND"])
        monkeypatch.setattr(http_module.random, "choice", lambda _seq: next(agents))

        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.host or "", request.headers.get("user-agent", "")))
            if request.url.host == "a.com":
                return httpx.Response(302, headers={"Location": "https://b.com/x"})
            return httpx.Response(200, text="ok")

        fetcher = _fetcher(clock, handler)
        fetcher.get_text("https://a.com/start")
        via_redirect = dict(seen)["b.com"]

        seen.clear()
        fetcher.get_text("https://b.com/direct")
        direct = dict(seen)["b.com"]

        assert via_redirect == direct, (
            f"目标主机两次拿到的 UA 不同（重定向 {via_redirect!r} vs 直接 {direct!r}）"
        )


class TestRedirectBodyIsNotRead:
    """被丢弃的重定向响应体不该被读进内存，更不该让请求失败。

    现象（修复前）：`_fetch_once` 对所有状态码都 `_read_capped`，而 `get` 收到 3xx
    时只从响应取 `Location` 头、正文从不消费 —— 于是那份被丢弃的体成了唯一读者。
    后果有二：正常重定向白白把整份体读进内存；体量超过 `max_bytes` 时直接把一次
    **本可成功**的重定向报成 `too_large`（决定成败的是被丢弃的 3xx 体，而不是最终
    目标页）。
    """

    def test_oversized_redirect_body_does_not_fail_the_fetch(self, clock: FakeClock) -> None:
        """302 的体超过 max_bytes 时，跟随仍应成功（体不被读）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/list":
                return httpx.Response(
                    302,
                    headers={"Location": "/final"},
                    content=b"x" * (128 * 1024),
                )
            return httpx.Response(200, text="<html>final</html>")

        fetcher = _fetcher(clock, handler, retries=1, max_bytes=1024)
        assert fetcher.get_text("https://a.com/list") == "<html>final</html>"

    def test_redirect_without_location_still_returns_body(self, clock: FakeClock) -> None:
        """3xx 但无 Location：`get` 会把它当最终响应，正文仍须读到。

        判据必须与 `get` 的「`status in _REDIRECT_STATUS and location`」一致 ——
        只按状态码分支会让这种少见的 3xx 返回空体。
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, content=b"<html>no location</html>")

        fetcher = _fetcher(clock, handler, retries=1)
        response = fetcher.get("https://a.com/x")
        assert response.status_code == 302
        assert response.content == b"<html>no location</html>"


class TestCompressedResponse:
    """带 `Content-Encoding` 的响应此前会**双重解压**而失败。

    现象（修复前）：`_read_capped` 用 `iter_bytes()` 读取 —— httpx 已按
    `Content-Encoding` 解压，拿到的 body 是**明文**；但 `_fetch_once` 重建
    `httpx.Response` 时把**原始响应头**（含 `Content-Encoding: gzip`）一并传了过去，
    httpx 于是在 `read()` 里对明文再解压一次 → `DecodingError`。它又是
    `httpx.HTTPError` 的子类，被 `except httpx.HTTPError` 兜成
    `FetchError("transport_failed")` —— 界面报「网络传输失败」，而网络完全正常。

    实测（本机，httpx 0.28.1）：6 个真实站点全部返回 gzip，其中 5 个因此报
    `transport_failed`，唯有 1 个响应未压缩的站点走完了读取。`Accept-Encoding:
    gzip` 是 httpx 的默认行为，所以这一族缺陷对**任何压缩站点**都必现 ——
    而 mock 响应此前从不带 `Content-Encoding`，门禁因此一直全绿。
    """

    def test_gzip_encoded_response_is_decoded(self, clock: FakeClock) -> None:
        """站点按 gzip 压缩时，抓取应成功并拿到正确明文。"""
        payload = "<html>中文测试</html>".encode()
        compressed = gzip.compress(payload)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip", "Content-Type": "text/html"},
                stream=httpx.ByteStream(compressed),
            )

        fetcher = _fetcher(clock, handler, retries=1)
        assert fetcher.get_text("https://a.com/x") == "<html>中文测试</html>"

    def test_deflate_encoded_response_is_decoded(self, clock: FakeClock) -> None:
        """deflate 与 gzip 同属 httpx 默认请求的编码，走同一条读取路径。"""
        payload = "<html>deflate 测试</html>".encode()
        compressed = zlib.compress(payload)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Encoding": "deflate", "Content-Type": "text/html"},
                stream=httpx.ByteStream(compressed),
            )

        fetcher = _fetcher(clock, handler, retries=1)
        assert fetcher.get_text("https://a.com/x") == "<html>deflate 测试</html>"

    def test_returned_response_does_not_claim_an_encoding(self, clock: FakeClock) -> None:
        """交出去的响应体已是明文，就不该再声称 `Content-Encoding`。

        这不是洁癖：`headers` 是调用方可见的公共面，一个「声称 gzip、实为明文」的
        响应会把这个坑原样转嫁给下一个读它的人（第二次 `.read()` 同样炸）。
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip", "Content-Type": "text/html"},
                stream=httpx.ByteStream(gzip.compress(b"<html>ok</html>")),
            )

        fetcher = _fetcher(clock, handler, retries=1)
        response = fetcher.get("https://a.com/x")
        assert "content-encoding" not in response.headers
        assert "transfer-encoding" not in response.headers

    def test_cap_applies_to_decompressed_body(self, clock: FakeClock) -> None:
        """体积上限卡的是**解压后**的大小，不是压缩体。

        判据：一个压缩后很小、解压后超过上限的响应必须报 `too_large`。
        若改为 `iter_raw()` 读压缩体，上限就只管压缩后的大小 —— 一个几 KB 的
        gzip 炸弹能解出任意大的明文，上限形同虚设。
        """
        # 高度可压缩：明文 200KB，gzip 后只有几百字节
        compressed = gzip.compress(b"x" * (200 * 1024))
        assert len(compressed) < 1024
        assert len(compressed) < 200 * 1024

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                stream=httpx.ByteStream(compressed),
            )

        fetcher = _fetcher(clock, handler, retries=1, max_bytes=1024)
        with pytest.raises(FetchError) as excinfo:
            fetcher.get_text("https://a.com/bomb")
        assert excinfo.value.code == "too_large"

    def test_gzip_redirect_without_location_still_returns_body(self, clock: FakeClock) -> None:
        """压缩的 3xx（无 Location）走同一条重建路径，同样不该炸。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"Content-Encoding": "gzip"},
                stream=httpx.ByteStream(gzip.compress(b"<html>no location</html>")),
            )

        fetcher = _fetcher(clock, handler, retries=1)
        response = fetcher.get("https://a.com/x")
        assert response.status_code == 302
        assert response.content == b"<html>no location</html>"
