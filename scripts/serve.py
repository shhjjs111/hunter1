"""开发期启动 Web UI —— 不装包也能跑。

    ./.tools/python/python.exe scripts/serve.py --db .data/hunter1.db --port 8000

为什么需要它：项目内自带的解释器是 Python **embeddable** 版，它用
`python312._pth` 接管 `sys.path`，**会忽略 `PYTHONPATH`**；同时 editable 安装
在它上面也无法完成（构建后端隔离失败）。所以开发期用显式路径注入把包挂上，
与 `examples/*.py` 的做法一致。

正式用户走 `pip install .` 后的 `hunter1 serve`，不经过这里。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hunter1.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["serve", *sys.argv[1:]]))
