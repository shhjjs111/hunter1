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


def check_page(*, limit: int, offset: int) -> None:
    """拒绝负数的 `limit` / `offset`。`limit == 0` 合法（= 不要任何行）。"""
    if limit < 0:
        raise ValueError(
            f"limit 不能为负（收到 {limit}）：SQLite 的 `LIMIT -1` 是**不限量**，"
            "会让「取一页」静默变成「取整库」。"
        )
    if offset < 0:
        raise ValueError(f"offset 不能为负（收到 {offset}）：负偏移没有定义。")


__all__ = ["check_page"]
