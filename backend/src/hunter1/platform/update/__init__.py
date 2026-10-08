"""自更新 —— 三合一（规则 / 用例 / IO）。

- `rules`：版本清单模型与比较规则（纯函数，无 IO）
- `service`：检查 / 准备的决策编排（网络经端口注入，可离线测试）
- `client`：HTTP 下载与校验（IO 半边）
- `ports`：自更新依赖的协议（ReleaseSource / ProgressCallback）

公开面导出全部；消费者统一 `from hunter1.platform.update import ...`，
不深链内部模块。
"""

from hunter1.platform.update.client import (
    DownloadError,
    ReleaseClient,
    download_verified,
    file_sha256,
)
from hunter1.platform.update.ports import ProgressCallback, ReleaseSource
from hunter1.platform.update.rules import (
    MAX_DOWNLOAD_BYTES,
    MAX_EXPANSION_RATIO,
    MAX_EXTRACTED_BYTES,
    ReleaseAsset,
    ReleaseManifest,
    is_newer,
    parse_version,
)
from hunter1.platform.update.service import (
    UpdateStatus,
    check_for_update,
    prepare_update,
)

__all__ = [
    "MAX_DOWNLOAD_BYTES",
    "MAX_EXPANSION_RATIO",
    "MAX_EXTRACTED_BYTES",
    "DownloadError",
    "ProgressCallback",
    "ReleaseAsset",
    "ReleaseClient",
    "ReleaseManifest",
    "ReleaseSource",
    "UpdateStatus",
    "check_for_update",
    "download_verified",
    "file_sha256",
    "is_newer",
    "parse_version",
    "prepare_update",
]
