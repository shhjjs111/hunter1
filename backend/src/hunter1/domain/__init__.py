"""领域层：纯模型与规则，**不依赖任何 IO**（无数据库 / 网络 / 文件系统）。

结构保证：本包不得 import `hunter1.infrastructure`。
依赖方向为 interfaces → application → domain；基础设施实现 domain 定义的端口。
"""

from __future__ import annotations

__all__: list[str] = []
