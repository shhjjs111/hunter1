"""领域层：纯模型与规则，**不依赖任何 IO**（无数据库 / 网络 / 文件系统）。

结构保证：本包不得 import `hunter1.application` / `hunter1.web` / `hunter1.crawlers` /
`hunter1.slices`；对 `hunter1.platform` 只允许 `platform.text`（纯函数）。
依赖方向为 interfaces → application → domain；基础设施实现经端口注入。

（迁移期：本包内容将在后续波次随各自切片归位到 `hunter1.slices.*`。）
"""

from __future__ import annotations

__all__: list[str] = []
