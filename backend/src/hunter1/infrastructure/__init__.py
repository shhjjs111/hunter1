"""基础设施层：外部依赖的实现（SQLite / LLM / 抓取 / 邮件）。

本层实现 domain 定义的端口，由应用层注入；不得被 domain import。
"""

from __future__ import annotations

__all__: list[str] = []
