"""HTTP 抓取器 —— httpx + 资源治理（限流/间隔/体积上限/robots）+ 重试 + UA 轮换。

错误一律以 `FetchError`（带机器可读 `code`）抛出，不静默返回 None ——
静默失败是旧系统最难查的一类问题（调用方拿到 None 却不知为什么）。

「对站点礼貌」的三个机制都在这里，但**默认关**（生产装配在 `main.AppContext.default`
打开）：每主机请求间隔（`min_interval`）、遵守 `robots.txt`（`respect_robots`）、
单响应体积上限（`max_bytes`，这个默认开 —— 它是防内存暴涨的硬约束，不是礼貌问题）。
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable

import httpx

from hunter1.platform.fetch.limits import (
    TRANSIENT_STATUS,
    HostLimiter,
    ResourceLimitTimeoutError,
)
from hunter1.platform.fetch.robots import ROBOTS_MAX_BYTES, RobotsCache

#: 单个响应体的上限。限流器管的是「同时打几个请求」，管不到「一个响应有多大」——
#: 一次重定向落到大文件（或站点返了个巨型日志页）就能把内存吃满。
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

DEFAULT_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

DEFAULT_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"

# `<meta charset=...>` 声明（「编码只写在 meta 里、响应头不带 charset」是中文站
# 常见形态）。标准要求它出现在前 1024 字节内，取 4KB 足够宽松。
_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""",
    re.IGNORECASE,
)

#: 「声明了也不能先信」的单字节编码。HTTP 默认值就是 ISO-8859-1，而大量中文站
#: 实际发 UTF-8（体里没有覆盖性的 charset 声明）。这些编码能解码任意字节序列、
#: 几乎不抛异常 —— 先采信它们等于把「乱码」当成「解码成功」，后面精心设计的
#: utf-8 / <meta> / gb18030 回退链一步都走不到。因此它们被排到 utf-8 与
#: `_META_CHARSET` 之后再试。
_UNRELIABLE_DECLARED = frozenset(
    {"iso-8859-1", "iso8859-1", "latin-1", "latin1", "latin_1", "cp1252", "windows-1252"}
)


class FetchError(RuntimeError):
    """一次抓取最终失败。`code` 是稳定的机器可读标识。"""

    def __init__(self, code: str, url: str) -> None:
        super().__init__(f"{code}: {url}")
        self.code = code
        self.url = url


def _decode_text(response: httpx.Response) -> str:
    """把响应体解成文本，带编码回退。

    为什么不用 `response.text`：httpx 在「响应头没有 charset、且环境里没有
    charset_normalizer / chardet」时固定按 utf-8 + `errors="replace"` 解码。中文站
    常把编码写在 `<meta>` 里、响应头不带 charset，于是整页**静默**变成一串 U+FFFD
    （本机实测：httpx 0.28.1、两个探测器均未安装）—— 而本层的承诺是「错误一律
    FetchError、不静默失败」。乱码不是异常，所以这里显式回退。
    """
    return _decode_bytes(response.content, declared=response.charset_encoding)


def _decode_bytes(content: bytes, *, declared: str | None) -> str:
    """解出响应体文本，带编码回退。

    优先级：**可信的声明** → 严格 utf-8 → 体里的 `<meta charset>` →
    **不可信的声明**（latin-1 家族）→ gb18030 / big5 → utf-8 宽松。

    为什么「声明」要分可信与不可信：HTTP 默认值就是 ISO-8859-1，而大量中文站
    实际发 UTF-8。latin-1 能解码任意字节序列、永不抛异常 —— 先采信它就等于把
    「乱码」当成「解码成功」（实测：declared=iso-8859-1 而体是 UTF-8 时得到
    `ä¸­æ\x96\x87`，而 declared=None 得到正确的 `中文测试`）。把这类编码压到
    utf-8 与 meta 之后，既修好「声明撒谎」的常见形态，也不冤枉真正用 latin-1
    的页面（它们会在第 4 步被正确解出）。
    """
    declared_norm = (declared or "").strip().lower()
    if declared_norm and declared_norm not in _UNRELIABLE_DECLARED:
        try:
            return content.decode(declared_norm)
        except (LookupError, UnicodeDecodeError):
            pass

    # 严格 utf-8 优先于「不可信的声明」—— 见上面的说明。
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        pass

    candidates: list[str] = []
    match = _META_CHARSET.search(content[:4096])
    if match:
        candidates.append(match.group(1).decode("ascii", "ignore"))
    # **先** 中文回退，**再** 不可信的声明。顺序反了会让 gb18030/big5 变成死代码：
    # latin-1 家族能解码任意字节序列、永不抛 UnicodeDecodeError，排在前面就必然
    # 在那里 return —— 响应头声明 `charset=iso-8859-1`（Apache 等对 text/* 的常见
    # 默认）而正文其实是 GBK 时，得到的是 mojibake 而不是正确文本，且无异常无日志，
    # 与「错误一律 FetchError、不静默失败」的承诺冲突。
    #
    # 取舍（诚实记录）：gb18030 覆盖面很广，对某些 latin-1/cp1252 页面也可能「解得出
    # 但解错」。本工具面向中文招聘站，`charset=iso-8859-1 但正文是 GBK` 是实测常见
    # 形态，而真用 latin-1 的页面极少 —— 因此把中文回退排前。若将来出现 latin-1 站点
    # 被解错的具体案例，应改为「按正文字节里的语言特征择档」，而不是简单调换顺序。
    candidates += ["gb18030", "big5"]
    if declared_norm:
        # 不可信的声明排在 utf-8、meta 与中文回退之后：只有前面都解不出时它才有价值。
        candidates.append(declared_norm)
    for encoding in candidates:
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    # 全都失败：退回 httpx 的宽松解码，至少不制造新的失败面。
    return content.decode("utf-8", "replace")


def _read_capped(response: httpx.Response, limit: int, url: str) -> bytes:
    """把响应体读进内存，但设上限；超限抛 `FetchError("too_large")`。

    刻意**不**静默截断：半截 HTML 会让解析器产出一个看起来正常、实际缺了一半的
    岗位列表 —— 比直接失败危险得多。
    """
    declared_length = response.headers.get("content-length")
    if declared_length and declared_length.isdigit() and int(declared_length) > limit:
        raise FetchError("too_large", url)
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes(64 * 1024):
        size += len(chunk)
        if size > limit:
            raise FetchError("too_large", url)
        chunks.append(chunk)
    return b"".join(chunks)


class HttpFetcher:
    """带重试与主机治理的 HTTP 客户端。可注入 transport 以便离线测试。"""

    def __init__(
        self,
        *,
        limiter: HostLimiter | None = None,
        timeout: float = 15.0,
        retries: int = 3,
        user_agents: Iterable[str] = DEFAULT_USER_AGENTS,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        min_interval: float = 0.0,
        respect_robots: bool = False,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        # 「对站点多礼貌」的两个开关默认**关**：平台层的默认值要保测试的确定性
        # （不然每个用例都要等间隔、每个主机都要多打一次 robots.txt）。
        # 生产装配在组装根打开它们（见 main.AppContext.default）。
        self.limiter = limiter or HostLimiter(min_interval=min_interval)
        self.timeout = max(1.0, float(timeout))
        self.retries = max(1, int(retries))
        self.user_agents = tuple(user_agents)
        self.max_bytes = max(1, int(max_bytes))
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self.timeout,
            transport=transport,
            follow_redirects=True,
        )
        self._robots = RobotsCache(self._fetch_robots_text) if respect_robots else None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpFetcher:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get_text(self, url: str, *, headers: dict[str, str] | None = None) -> str:
        """抓取并返回响应文本（带编码回退）。失败抛 `FetchError`。"""
        return _decode_text(self.get(url, headers=headers))

    def _fetch_robots_text(self, url: str) -> str | None:
        """取 robots.txt 原文；不可用（4xx / 5xx / 网络错误）返回 None。

        刻意不走 `get_text`：那会把 robots.txt 的 404 记进限流器的失败统计、
        也会重试三次 —— 而「站点没写 robots.txt」是完全正常的情况。
        """
        try:
            response = self._client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        body = response.content[:ROBOTS_MAX_BYTES]
        return _decode_bytes(body, declared=response.charset_encoding)

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        request_headers = {
            "User-Agent": random.choice(self.user_agents),
            "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if headers:
            request_headers.update(headers)

        if self._robots is not None and not self._robots.allows(
            url, user_agent=request_headers["User-Agent"]
        ):
            # 站点明确说不许抓 → 不重试、也不记成主机失败（它不是故障）
            raise FetchError("robots_disallowed", url)

        last_code = "transport_failed"
        for attempt in range(1, self.retries + 1):
            try:
                with (
                    self.limiter.acquire(url, timeout=self.timeout),
                    self._client.stream("GET", url, headers=request_headers) as stream,
                ):
                    status = stream.status_code
                    body = _read_capped(stream, self.max_bytes, url)
                    response = httpx.Response(
                        status,
                        headers=stream.headers,
                        content=body,
                        request=stream.request,
                    )
            except (ValueError, httpx.InvalidURL) as exc:
                # 畸形 URL 没有可治理的主机：限流器 fail-closed 拒绝（ValueError），
                # httpx 也可能在更深处拒绝（InvalidURL）。统一翻译成 FetchError ——
                # 「错误一律 FetchError」是这层的承诺，不让别的形状漏出去。
                raise FetchError("invalid_url", url) from exc
            except ResourceLimitTimeoutError:
                last_code = "resource_timeout"
                if attempt >= self.retries:
                    raise FetchError(last_code, url) from None
                continue
            except FetchError:
                # 体积超限：重试也还是这么大，直接报出去
                raise
            except httpx.HTTPError:
                self.limiter.record(url, failed=True)
                last_code = "transport_failed"
                if attempt >= self.retries:
                    raise FetchError(last_code, url) from None
                continue

            self.limiter.record(
                url,
                status=response.status_code,
                retry_after=response.headers.get("Retry-After"),
            )

            if response.status_code in TRANSIENT_STATUS:
                last_code = f"http_{response.status_code}"
                if attempt >= self.retries:
                    raise FetchError(last_code, url)
                continue

            if response.status_code >= 400:
                # 客户端错误（4xx，非 408/429）不重试 —— 重试也不会变好
                raise FetchError(f"http_{response.status_code}", url)

            return response

        raise FetchError(last_code, url)


__all__ = [
    "DEFAULT_ACCEPT_LANGUAGE",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_USER_AGENTS",
    "FetchError",
    "HttpFetcher",
]
