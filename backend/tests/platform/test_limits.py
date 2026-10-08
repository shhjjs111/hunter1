"""抓取资源治理单元测试 —— 全部离线，用注入的假时钟/假睡眠。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
"""

from __future__ import annotations

import pytest

from hunter1.platform.fetch.limits import (
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
        """退避长到 cap 就**停在 cap**，而不是「不超过 cap」。

        原先断言 `<= 4.0`：与「不冷却」重合 —— record 完全不上冷却时
        `cooldown_remaining` 返回 0.0，`0.0 <= 4.0` 照样绿。这里钉精确值：
        base=1.0、cap=4.0、连续 10 次 500 → 理论退避 2^9 已被夹到 cap。
        """
        limiter = _limiter(clock, per_host=2, base=1.0, cap=4.0)
        for _ in range(10):
            limiter.record("https://a.com/x", status=500)
        assert limiter.cooldown_remaining("a.com") == pytest.approx(4.0)

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

    def test_bad_retry_after_falls_back_to_the_backoff(self, clock: FakeClock) -> None:
        """坏 `Retry-After` 要被**忽略**、退回指数退避 —— 而不是「不冷却」。

        原先断言 `<= 60.0`：与「退避被 cap 夹住」那条不变量重合，完全不上冷却
        （0.0 <= 60.0）也绿，`_parse_retry_after` 坏掉也绿。这里钉住**具体时长**：
        首次失败、base=1.0 → 冷却恰好 1.0 秒（= base × 2^(1-1)）。
        """
        limiter = _limiter(clock, per_host=2, base=1.0, cap=60.0)
        limiter.record("https://a.com/x", status=429, retry_after="not-a-number")
        assert limiter.cooldown_remaining("a.com") == pytest.approx(1.0)

    def test_valid_retry_after_longer_than_backoff_wins(self, clock: FakeClock) -> None:
        """合法的 `Retry-After` 比退避更长时以它为准（主机明说了要等多久）。

        与上一条配对：一条钉「坏值被忽略」，一条钉「好值被采纳」——
        只钉前者的写法在「永远忽略 retry_after」的实现下照样绿。
        """
        limiter = _limiter(clock, per_host=2, base=1.0, cap=60.0)
        limiter.record("https://a.com/x", status=429, retry_after="30")
        assert limiter.cooldown_remaining("a.com") == pytest.approx(30.0)


class TestRequestSpacing:
    """`min_interval`：同一主机的两次请求之间至少间隔这么久（默认 0 = 不等待）。

    这是「对站点礼貌」里最容易被跳过的一环：并发/退避管的是「别打太密」，但一次
    抓取一轮只有一个进程、一个接一个地打 —— 没有间隔就是连发。生产装配在组装根
    打开它（默认关，保测试的确定性）。
    """

    def _spaced(self, clock: FakeClock, *, interval: float = 1.0) -> HostLimiter:
        return HostLimiter(
            per_host=2,
            min_interval=interval,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    def test_second_request_on_same_host_waits(self, clock: FakeClock) -> None:
        limiter = self._spaced(clock)
        with limiter.acquire("https://a.com/1"):
            pass
        before = clock.now
        with limiter.acquire("https://a.com/2"):
            pass
        assert clock.now - before >= 1.0, "同一主机的第二次请求没有等待间隔"

    def test_different_hosts_do_not_wait_for_each_other(self, clock: FakeClock) -> None:
        limiter = self._spaced(clock)
        with limiter.acquire("https://a.com/1"):
            pass
        before = clock.now
        with limiter.acquire("https://b.com/1"):
            pass
        assert clock.now == before, "间隔约束不该跨主机生效"

    def test_default_interval_is_zero(self, clock: FakeClock) -> None:
        """默认不额外等待 —— 平台层的默认值要保测试与脚本的确定性/速度。"""
        limiter = _limiter(clock)
        with limiter.acquire("https://a.com/1"):
            pass
        before = clock.now
        with limiter.acquire("https://a.com/2"):
            pass
        assert clock.now == before

    def test_negative_interval_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            HostLimiter(min_interval=-1.0)


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
