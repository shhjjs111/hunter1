"""库里字段值的容错还原 —— 数值越界 / 时间倒序不该拖垮整张表。

与 `enums.py` 的 `restore_enum` 同源、同因：行映射（row → domain）直接构造领域
对象时，一旦库里出现越界值，pydantic 就抛 `ValidationError` —— 而它发生在
**读列表**的路径上：一行坏数据让整个岗位库 / 投递列表 500，用户既看不到是哪一行，
也没有修库的入口。

`restore_enum` 只覆盖了枚举列，数值列（`match_score`）与时间不变量
（`first_seen_at <= last_seen_at`、`applied_at <= updated_at`）当时没有跟上 ——
同族的缺口只修一半，另一半照样能把整表读挂（实测：`UPDATE jobs SET match_score=150`
之后 `GET /api/jobs` 直接 500）。

非法值只可能来自手工改库、旧版本残留、或外部工具写入 —— 不属于正常流程，但也不该
把整张表一起拖垮。约定与 `Database.initialize` 的重复序号修复、`restore_enum` 一致：
**容忍/改动了用户数据就必须留痕**（stderr 一行），不能静默 —— 否则将来排查
「这个分数/时间怎么自己变了」时毫无线索。
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import overload

#: 越界/倒序时统一追加的说明。与 `enums.py` 的措辞保持一致，便于按关键词检索。
_ORIGIN_HINT = "（来自手工改库或更早的版本；正常流程不会产生这种值）"


def restore_int_in_range(
    value: object,
    *,
    minimum: int,
    maximum: int,
    default: int | None = None,
    where: str,
) -> int | None:
    """把库里的整数收敛到 `[minimum, maximum]`；越界/非整数退回 `default` 并留痕。

    `None` 原样透传（它是合法取值 —— 例如「未评分」）。`bool` 被显式排除：
    `True`/`False` 是 `int` 的子类，若不排除，库里一个 `1`/`0` 会冒充合法整数
    （`isinstance(True, int)` 为真），而它多半是写入事故而非有意值。
    """
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not (minimum <= value <= maximum):
        print(
            f"警告：{where} 的值非法（{value!r}），按"
            f"「{default if default is not None else '未设置'}」处理。{_ORIGIN_HINT}",
            file=sys.stderr,
        )
        return default
    return value


@overload
def restore_time_order(
    earlier: datetime, later: datetime, *, where: str
) -> tuple[datetime, datetime]: ...


@overload
def restore_time_order(
    earlier: datetime | None, later: datetime | None, *, where: str
) -> tuple[datetime | None, datetime | None]: ...


def restore_time_order(
    earlier: datetime | None,
    later: datetime | None,
    *,
    where: str,
) -> tuple[datetime | None, datetime | None]:
    """保证 `earlier <= later`；倒序（或时区混用）时把 `earlier` 对齐到 `later`。

    任一为 `None` 时原样返回（调用方的模型可能允许半空）。倒序说明两份时间戳里
    至少有一个是坏的，而**保留 `later`** 是更安全的选择：`last_seen_at` / `updated_at`
    是「这条记录最近一次被触碰」的证据，宁可让上界保守，也不要伪造一个更早的下界。

    naive 与 aware 混用时裸比较抛 `TypeError`（不是 `ValueError`）—— 同样按「坏行」
    处理，不让它直穿成 500。

    重载（两个入参都非 `None` 时返回非 `None`）是为了让调用方保留类型：`Job` 的时间
    可空、`Application.applied_at` / `updated_at` 不可空 —— 同一个帮手要能喂给两边，
    否则要么调用方加 `assert`，要么类型检查失真。
    """
    if earlier is None or later is None:
        return earlier, later
    try:
        in_order = earlier <= later
    except TypeError:
        in_order = False
    if in_order:
        return earlier, later
    print(
        f"警告：{where} 的时间倒序（另有更早时间晚于更晚时间），已按较晚的时间对齐。{_ORIGIN_HINT}",
        file=sys.stderr,
    )
    return later, later


__all__ = ["restore_int_in_range", "restore_time_order"]
