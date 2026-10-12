"""控制台输出编码 —— 把 stdout/stderr 固定到 UTF-8。

为什么需要它：Windows 控制台按**本地代码页**编码输出。简中 Windows 是 CP936（GBK，
能编码中文），而英文 Windows（含 **GitHub 的 windows runner**）是 **cp1252 —— 它
编码不了任何中文**。于是任何 `print("中文…")` 在 cp1252 控制台上直接抛
`UnicodeEncodeError: 'charmap' codec can't encode characters`。

这个坑在本仓库出现过多次，且**形态各异**（都源于「每个入口各自持一份拷贝」）：

- `examples/demo_sites.py` 打印真实站点标题 —— 在简中终端上崩过（CP936 也有编不了的
  字符），于是那里加了防护；
- `hunter1.spec` 打印「本次打包不含界面」告警 —— 在第一行就崩；
- 其余三个示例（`demo_assistant` / `demo_crawl` / `demo_persist`）**从未有防护** ——
  它们在本机（CP936，能编码中文）一直绿，直到 CI 第一次真跑在 **windows-latest**
  （cp1252）上才暴露：`examples/demo_assistant.py` 的 `print(f"岗位库已就绪：…")`
  抛 `UnicodeEncodeError`，把整个门禁打红。

治根的做法不是「再多抄一份」，而是**一处定义、各处引用**。本模块零依赖（只用标准库），
因此 `cli`、示例都能安全导入，不必担心拖进重依赖。
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterable
from typing import Any


def enable_utf8_console(*streams: Any) -> None:
    """把输出流固定到 UTF-8，并（在 Windows 上）请控制台配合。

    不传 `streams` 时处理 `sys.stdout` / `sys.stderr`。

    两点讲究：

    1. `reconfigure(encoding="utf-8", errors="replace")` 在**所有平台**都做 ——
       不只是 Windows。CI 上的英文 runner 就是非中文 locale，靠的正是这一步把
       cp1252 的 stdout 换成 UTF-8（所以「只在控制台设代码页」是不够的）。
    2. 任何一步失败都**静默跳过**：拿不到控制台（输出被重定向到文件/管道）、
       `reconfigure` 不可用或抛错 —— 显示不好是小事，不能因此让程序起不来。
       （这条不可退让：本函数处于每个入口的最前面，它自己崩就等于程序崩。）
    """
    targets: Iterable[Any] = streams or (sys.stdout, sys.stderr)

    if os.name == "nt":
        with contextlib.suppress(Exception):
            # 拿不到控制台时会抛（比如输出被重定向到文件）
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    for stream in targets:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


__all__ = ["enable_utf8_console"]
