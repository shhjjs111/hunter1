"""库里枚举值的容错还原。

行映射（row → domain）里直接构造枚举，一旦库里出现非法值就抛 `ValueError` ——
而它发生在**读列表**的路径上：一行坏数据让整个岗位库 / 投递列表 500，用户既看不到
是哪一行，也没有修库的入口。

非法值只可能来自手工改库、旧版本残留、更早的枚举名 —— 不属于正常流程，但也不该
把整张表一起拖垮。容忍的约定与 `Database.initialize` 的重复序号修复一致：
**容忍/改动了用户数据就必须留痕**（stderr 一行），不能静默 —— 否则将来排查
「状态怎么自己变了」时毫无线索。
"""

from __future__ import annotations

import sys
from enum import StrEnum


def restore_enum[E: StrEnum](enum: type[E], raw: str, *, default: E, where: str) -> E:
    """把库里的字符串还原成枚举；非法值退回到 `default` 并打一行 stderr。

    `where` 是给人看的位置说明（如「岗位抓取状态」），出现在警告里。
    """
    try:
        return enum(raw)
    except ValueError:
        print(
            f"警告：{where} 的值非法（{raw!r}），按「{default.value}」处理。"
            "（来自手工改库或更早的版本；正常流程不会产生这种值）",
            file=sys.stderr,
        )
        return default


__all__ = ["restore_enum"]
