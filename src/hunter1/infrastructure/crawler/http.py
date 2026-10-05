"""HTTP 抓取器 —— httpx + 资源治理 + 重试 + 浏览器 UA 轮换。

错误一律以 `FetchError`（带机器可读 `code`）抛出，不静默返回 None ——
静默失败是旧系统最难查的一类问题（调用方拿到 None 却不知为什么）。
"""

from __future__ import annotations

import random
from collections.abc import Iterable

import httpx

from hunter1.infrastructure.crawler.limits import (
    TRANSIENT_STATUS,
    HostLimiter,
    ResourceLimitTimeoutError,
)

DEFAULT_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

DEFAULT_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"


class FetchError(RuntimeError):
    """一次抓取最终失败。`code` 是稳定的机器可读标识。"""

    def __init__(self, code: str, url: str) -> None:
        super().__init__(f"{code}: {url}")
        self.code = code
        self.url = url


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
    ) -> None:
        self.limiter = limiter or HostLimiter()
        self.timeout = max(1.0, float(timeout))
        self.retries = max(1, int(retries))
        self.user_agents = tuple(user_agents)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self.timeout,
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpFetcher:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get_text(self, url: str, *, headers: dict[str, str] | None = None) -> str:
        """抓取并返回响应文本。失败抛 `FetchError`。"""
        return self.get(url, headers=headers).text

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        request_headers = {
            "User-Agent": random.choice(self.user_agents),
            "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if headers:
            request_headers.update(headers)

        last_code = "transport_failed"
        for attempt in range(1, self.retries + 1):
            try:
                with self.limiter.acquire(url, timeout=self.timeout):
                    response = self._client.get(url, headers=request_headers)
            except ResourceLimitTimeoutError:
                last_code = "resource_timeout"
                if attempt >= self.retries:
                    raise FetchError(last_code, url) from None
                continue
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


__all__ = ["DEFAULT_ACCEPT_LANGUAGE", "DEFAULT_USER_AGENTS", "FetchError", "HttpFetcher"]
