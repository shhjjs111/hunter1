"""抓取资源治理 —— 每主机并发限流 + 失败冷却 + 指数退避。

借鉴旧系统 `recruitment_core/resources.py` 的设计（host 冷却 + 并发池），
但改为**进程内、可注入时钟**，以便完全离线单测（旧系统用文件锁，测试困难）。

不变量：
- 同一主机同时在飞的请求数 ≤ `per_host`；
- 某主机失败后进入冷却窗口，窗口内对该主机的 acquire 会等待/超时；
- 冷却时长随连续失败指数增长，且有上限；成功清零；
- 冷却按主机隔离，不互相影响。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# 这些状态码视为「服务端暂时不可用」，触发冷却
TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class ResourceLimitTimeoutError(RuntimeError):
    """在给定预算内没能获得资源许可（并发满 或 处于冷却）。"""


@dataclass
class _HostState:
    in_flight: int = 0
    failures: int = 0
    cooldown_until: float = 0.0


@dataclass
class _Lease:
    _release: Callable[[], None] = field(repr=False)
    _released: bool = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._release()

    def __enter__(self) -> _Lease:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class HostLimiter:
    """按主机维度的并发/冷却治理器。线程安全。"""

    def __init__(
        self,
        *,
        per_host: int = 2,
        backoff_base: float = 1.0,
        backoff_cap: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if per_host < 1:
            raise ValueError("per_host must be >= 1")
        self.per_host = per_host
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._hosts: dict[str, _HostState] = {}

    # ---- 对外查询 ----

    def cooldown_remaining(self, host: str) -> float:
        with self._lock:
            state = self._hosts.get(host)
            if state is None:
                return 0.0
            return max(0.0, state.cooldown_until - self._monotonic())

    def cooldown_until(self, host: str) -> float:
        with self._lock:
            state = self._hosts.get(host)
            return state.cooldown_until if state is not None else 0.0

    def failure_count(self, host: str) -> int:
        with self._lock:
            state = self._hosts.get(host)
            return state.failures if state is not None else 0

    # ---- 获取许可 ----

    def acquire(self, url: str, *, timeout: float = 30.0) -> _Lease:
        """获取一次对 `url` 所在主机的请求许可。

        冷却中或并发已满时会等待（用注入的 sleep），超出 `timeout` 抛
        `ResourceLimitTimeoutError`。
        """
        host = _host_of(url)
        deadline = self._monotonic() + max(0.0, timeout)

        while True:
            with self._lock:
                state = self._hosts.setdefault(host, _HostState())
                now = self._monotonic()
                cooling = state.cooldown_until > now
                has_slot = state.in_flight < self.per_host
                if not cooling and has_slot:
                    state.in_flight += 1
                    return _Lease(_release=lambda: self._release(host))

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise ResourceLimitTimeoutError(f"resource admission timed out: {host}")
            self._sleep(min(0.025, remaining))

    def _release(self, host: str) -> None:
        with self._lock:
            state = self._hosts.get(host)
            if state is not None and state.in_flight > 0:
                state.in_flight -= 1

    # ---- 记录结果 ----

    def record(
        self,
        url: str,
        *,
        status: int | None = None,
        retry_after: str | None = None,
        failed: bool = False,
    ) -> None:
        """记录一次请求结果；失败则按指数退避设置冷却。"""
        host = _host_of(url)
        is_failure = failed or (status is not None and status in TRANSIENT_STATUS)

        with self._lock:
            state = self._hosts.setdefault(host, _HostState())
            if not is_failure:
                # 成功只重置失败计数（让下次退避从基数重来），**不取消已生效的冷却**。
                # 并发中的一次 200 不能证明主机没在限流我们 —— 它可能是限流生效前
                # 就已发出的请求。尊重 429/5xx 退避是对主机的礼貌，也是避免被封的关键。
                state.failures = 0
                return

            state.failures = min(state.failures + 1, 20)
            delay = min(self.backoff_cap, self.backoff_base * (2 ** (state.failures - 1)))
            if retry_after:
                parsed = _parse_retry_after(retry_after)
                if parsed is not None:
                    delay = max(delay, min(parsed, self.backoff_cap))
            state.cooldown_until = max(state.cooldown_until, self._monotonic() + delay)


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower() or "unknown"


def _parse_retry_after(value: str) -> float | None:
    """解析 Retry-After（秒数形式）。日期形式暂不支持，返回 None。"""
    try:
        seconds = float(value.strip())
    except (ValueError, AttributeError):
        return None
    return seconds if seconds >= 0 else None


__all__ = ["TRANSIENT_STATUS", "HostLimiter", "ResourceLimitTimeoutError"]
