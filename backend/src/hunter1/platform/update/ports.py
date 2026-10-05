"""自更新的端口契约 —— 检查与下载依赖的抽象。

只保留**进程边界**所需的协议：自更新要访问网络（取清单 / 下载产物），
决策逻辑（`service.py`）不碰传输细节，因此可离线测试。

（这两个类型原先住在 `application/ports.py`；自更新归位到 platform 后随行 ——
端口跟着它的消费者走。）
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from hunter1.platform.update.rules import ReleaseAsset, ReleaseManifest

# 下载进度回调：(已收字节数, 总字节数或 None)
ProgressCallback = Callable[[int, int | None], None]


@runtime_checkable
class ReleaseSource(Protocol):
    """版本清单与发布产物的来源（自更新用）。

    决策逻辑只依赖这两个方法，所以「检查更新」可以完全离线测试；
    真实实现见 `platform.update.client.ReleaseClient`。

    `download_asset` 的 `on_progress` 是契约的一部分（不是实现私有的扩展点）——
    否则「按端口实现」的替身/第三方实现会悄悄缺少进度能力。
    """

    def fetch_manifest(self, url: str) -> ReleaseManifest: ...

    def download_asset(
        self,
        asset: ReleaseAsset,
        dest: Path,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> Path: ...


__all__ = ["ProgressCallback", "ReleaseSource"]
