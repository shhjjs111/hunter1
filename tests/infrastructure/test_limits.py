"""抓取资源治理单元测试 —— 全部离线，用注入的假时钟/假睡眠。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

import pytest

from hunter1.infrastructure.crawler.limits import (
    HostLimiter,
    ResourceLimitTimeoutError,
)
from tests.conftest import FakeClock


def _limiter(
    clock: FakeClock, *, per_host: int = 2, base: float = 1.0, cap: float = 8.0
) -> HostLimiter:
    return HostLimiter(
        per_host=per_host,
        backoff_base=base,
        backoff_cap=cap,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


class TestAcquire:
    def test_acquires_up_to_limit(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=2)
        lease1 = limiter.acquire("https://a.com/x", timeout=5)
        lease2 = limiter.acquire("https://a.com/y", timeout=5)
        assert lease1 is not None and lease2 is not None
        lease1.release()
        lease2.release()

    def test_blocks_then_times_out_when_saturated(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=1)
        held = limiter.acquire("https://a.com/x", timeout=5)
        with pytest.raises(ResourceLimitTimeoutError):
            limiter.acquire("https://a.com/y", timeout=2)
        held.release()

    def test_release_frees_a_slot(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=1)
        first = limiter.acquire("https://a.com/x", timeout=5)
        first.release()
        second = limiter.acquire("https://a.com/y", timeout=5)  # 不应超时
        second.release()

    def test_different_hosts_do_not_share_slots(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=1)
        a = limiter.acquire("https://a.com/x", timeout=5)
        b = limiter.acquire("https://b.com/x", timeout=5)  # 另一台主机，独立配额
        a.release()
        b.release()

    def test_release_is_idempotent(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=1)
        lease = limiter.acquire("https://a.com/x", timeout=5)
        lease.release()
        lease.release()  # 不应抛错
        again = limiter.acquire("https://a.com/x", timeout=5)
        again.release()


class TestCooldown:
    def test_failure_puts_host_in_cooldown(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=2, base=1.0)
        limiter.record("https://a.com/x", status=503)
        with pytest.raises(ResourceLimitTimeoutError):
            limiter.acquire("https://a.com/x", timeout=0.5)

    def test_cooldown_expires_with_time(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=2, base=2.0)
        limiter.record("https://a.com/x", status=429)
        clock.now += 3.0  # 越过退避窗口
        lease = limiter.acquire("https://a.com/x", timeout=1)
        lease.release()

    def test_backoff_grows_with_consecutive_failures(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=2, base=1.0, cap=60.0)
        limiter.record("https://a.com/x", status=500)
        first_until = limiter.cooldown_until("a.com")
        limiter.record("https://a.com/x", status=500)
        second_until = limiter.cooldown_until("a.com")
        assert second_until > first_until

    def test_backoff_is_capped(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=2, base=1.0, cap=4.0)
        for _ in range(10):
            limiter.record("https://a.com/x", status=500)
        assert limiter.cooldown_remaining("a.com") <= 4.0

    def test_retry_after_header_is_honoured(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=2, base=1.0, cap=60.0)
        limiter.record("https://a.com/x", status=429, retry_after="10")
        assert limiter.cooldown_remaining("a.com") >= 10.0

    def test_success_resets_failure_count(self, clock: FakeClock) -> None:
        """成功请求重置失败计数 —— 使退避不再指数增长。"""
        limiter = _limiter(clock, per_host=2, base=1.0, cap=60.0)
        limiter.record("https://a.com/x", status=500)
        limiter.record("https://a.com/x", status=200)
        assert limiter.failure_count("a.com") == 0

    def test_success_does_not_cancel_active_cooldown(self, clock: FakeClock) -> None:
        """并发中的一次 200 不取消已生效的冷却。

        理由（fail-closed 优先）：并发请求里的 200 不能证明主机没在限流我们。
        它可能是限流生效前就已发出的。尊重 429/5xx 退避是对主机礼貌、
        也是避免 IP 被封的关键。
        """
        limiter = _limiter(clock, per_host=2, base=1.0, cap=60.0)
        limiter.record("https://a.com/x", status=429)
        assert limiter.cooldown_remaining("a.com") > 0
        limiter.record("https://a.com/x", status=200)
        assert limiter.cooldown_remaining("a.com") > 0  # 冷却仍在

    def test_success_prevents_backoff_growth(self, clock: FakeClock) -> None:
        """成功使下次失败从退避基数重新开始，而非继续指数上升。"""
        limiter = _limiter(clock, per_host=2, base=2.0, cap=60.0)
        limiter.record("https://a.com/x", status=500)  # failures=1
        clock.now += 3.0  # 越过冷却窗口
        limiter.record("https://a.com/x", status=200)  # failures 归零
        clock.now += 1.0
        limiter.record("https://a.com/x", status=500)  # failures 重新为 1
        assert limiter.cooldown_remaining("a.com") <= 2.0 + 1e-6

    def test_cooldown_is_per_host(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=2)
        limiter.record("https://a.com/x", status=503)
        lease = limiter.acquire("https://b.com/x", timeout=1)  # 不受影响
        lease.release()

    def test_bad_retry_after_is_ignored(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=2, base=1.0, cap=60.0)
        limiter.record("https://a.com/x", status=429, retry_after="not-a-number")
        assert limiter.cooldown_remaining("a.com") <= 60.0


class TestMalformedUrls:
    """解析不出主机的 URL 必须被拒绝 —— 不能塞进共享的 "unknown" 桶。

    否则一个坏 URL 的失败会连带所有「无主机」请求一起冷却（per_host=2 的
    共享槽位），一处畸形值拖累整轮抓取。
    """

    def test_malformed_url_is_rejected(self, clock: FakeClock) -> None:
        limiter = _limiter(clock, per_host=2)
        with pytest.raises(ValueError):
            limiter.acquire("not a url", timeout=0.5)
        with pytest.raises(ValueError):
            limiter.record("not a url", status=503)

    def test_malformed_url_does_not_inflict_real_hosts(self, clock: FakeClock) -> None:
        """一个坏 URL 的失败不能牵连真实主机的配额/冷却。"""
        limiter = _limiter(clock, per_host=2)
        with pytest.raises(ValueError):
            limiter.record("https://", status=503)  # netloc 为空
        lease = limiter.acquire("https://a.com/x", timeout=1)
        lease.release()
