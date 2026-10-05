"""`python -m hunter1` 入口 —— 等价于 `hunter1` 命令。

在 `src` 布局下，未做 editable 安装时 `hunter1` 脚本不存在；
`python -m hunter1`（配合 PYTHONPATH=src）是不装也能用的那条路。
"""

from __future__ import annotations

import sys

from hunter1.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
