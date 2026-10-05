"""业务垂直切片 —— 每个子包 = 一个 Agent 领地（见 AGENTS.md）。

切片间只经公开面（`__init__.py`）互相引用；依赖方向由
`tests/test_architecture.py` 钉死。
"""

from __future__ import annotations

__all__: list[str] = []
