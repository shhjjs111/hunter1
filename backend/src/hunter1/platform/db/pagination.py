"""分页参数的共同守卫 —— 仓储是最后一道防线。

这里**拒绝而不是钳制**：钳制会把「调用方算错了」变成「悄悄换了个窗口」，而分页窗口
悄悄变化的表现是漏行 / 重行，比直接报错难查得多。

为什么必须有这一道：SQLite 的 `LIMIT -1` 含义是**不限量**（不是空页），`OFFSET -5`
同样没有定义。调用方漏了校验时，一个负值就让「取一页」变成「取整库」—— 正好与
调用方的意图相反，而且没有任何迹象。

（路由层已经用 `Query(ge=...)` 挡了一道；这里不是重复，是让**直接调用仓储**的
内部代码（服务、脚本、测试）也走同一条判据。）
"""

from __future__ import annotations

#: 绑进 SQL 的整数上界（SQLite 是 int64）。超过它的 `limit` / `offset` 会被
#: sqlite3 绑参抛 `OverflowError` —— 那是 `Exception` 直系，**不是** `ValueError`，
#: 于是既不被这里的判据拦住、也不被只认 ValueError 的调用方接住，最终以 500 穿透。
#: 与「拒绝而不是钳制」同一取向：宁可当场说清「这个值不合法」。
SQLITE_INT_MAX = 2**63 - 1


def check_page(*, limit: int, offset: int) -> None:
    """拒绝越界的 `limit` / `offset`。`limit == 0` 合法（= 不要任何行）。

    两条轴：**负数**（SQLite 里 `LIMIT -1` 是不限量、负偏移无定义）与**上界**
    （超出 int64 会在绑参时以 `OverflowError` 穿透成 500）。两者都不是「钳制一下
    就好」—— 钳制会把「调用方算错了」变成「悄悄换了个窗口」，而窗口悄悄变化的
    表现是漏行 / 重行，比直接报错难查得多。
    """
    if limit < 0:
        raise ValueError(
            f"limit 不能为负（收到 {limit}）：SQLite 的 `LIMIT -1` 是**不限量**，"
            "会让「取一页」静默变成「取整库」。"
        )
    if offset < 0:
        raise ValueError(f"offset 不能为负（收到 {offset}）：负偏移没有定义。")
    if limit > SQLITE_INT_MAX:
        raise ValueError(f"limit 超出 int64 上界（收到 {limit}）：绑进 SQL 会抛 OverflowError。")
    if offset > SQLITE_INT_MAX:
        raise ValueError(f"offset 超出 int64 上界（收到 {offset}）：绑进 SQL 会抛 OverflowError。")


__all__ = ["SQLITE_INT_MAX", "check_page"]
