"""pytest 共享 fixture。"""

from __future__ import annotations

import pytest


class FakeClock:
    """可手动推进的单调时钟 + 记录 sleep。

    用于让「限流 / 冷却 / 退避」这类时间相关逻辑可以**确定性、零等待**地测试。
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()
