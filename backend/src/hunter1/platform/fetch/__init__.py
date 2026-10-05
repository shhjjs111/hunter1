"""基础设施 · 抓取层（HTTP 客户端 + 资源治理 + 适配器）。

本层实现 domain 定义的抓取契约，由应用层注入使用。
"""

from __future__ import annotations

from hunter1.platform.fetch.limits import (
    TRANSIENT_STATUS,
    HostLimiter,
    ResourceLimitTimeoutError,
)

__all__ = ["TRANSIENT_STATUS", "HostLimiter", "ResourceLimitTimeoutError"]
