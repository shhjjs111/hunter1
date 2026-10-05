"""应用层：用例编排（抓取 / 评分 / 投递 / 求职助手）。

只依赖 domain 与注入的端口（Protocol），不直接接触具体基础设施实现。

（迁移期：本包内容将在后续波次随各自切片归位到 `hunter1.slices.*`。）
"""

from __future__ import annotations

__all__: list[str] = []
