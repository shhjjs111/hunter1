"""sync 切片 —— 手机独立版与电脑端之间的数据快照同步。

公开面（其他切片只允许从这里 import 符号）：

- `SyncStore`：快照导出 / 合并
- `build_router`：HTTP 面工厂
- `SyncSnapshot` / `SyncMergeResponse` 等：契约模型

同步只搬用户自己的求职数据（岗位、投递、候选人画像），**不搬 API Key 与
聊天记录** —— 前者是设备凭据，后者是本地会话历史，都不该跨设备复制。

详见 `SLICE.md`。
"""

from hunter1.slices.sync.router import build_router
from hunter1.slices.sync.schemas import (
    SyncApplication,
    SyncApplicationImport,
    SyncCounts,
    SyncJob,
    SyncJobImport,
    SyncMergeResponse,
    SyncProfile,
    SyncSnapshot,
    SyncSnapshotImport,
)
from hunter1.slices.sync.store import SyncStore

__all__ = [
    "SyncApplication",
    "SyncApplicationImport",
    "SyncCounts",
    "SyncJob",
    "SyncJobImport",
    "SyncMergeResponse",
    "SyncProfile",
    "SyncSnapshot",
    "SyncSnapshotImport",
    "SyncStore",
    "build_router",
]
