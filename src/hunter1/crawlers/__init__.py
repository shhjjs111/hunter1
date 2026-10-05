"""站点抓取适配器：一个招聘站点一个模块。

每个适配器实现统一的 `Crawler` 协议（见 domain/ports），由 `infrastructure/crawler`
统一调度、限流与资源治理。新增站点 = 新增一个文件 + 注册一行，不改核心代码。

本目录在 M2 阶段开始填充（迁移旧系统的 64 个适配器）。
"""

from __future__ import annotations

__all__: list[str] = []
