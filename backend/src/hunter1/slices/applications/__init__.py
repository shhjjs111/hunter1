"""applications 切片 —— 投递记录的事实（记录投递 / 列表 / 阶段推进 / 删除）。

公开面（其他切片只允许从这里 import 符号）：

- `ApplicationStore`：数据存取门面
- `build_router`：HTTP 面工厂（组装处注入依赖）
- `schemas`：API 模型（契约源头）
- `service`：用例函数（apply_to_job / new_application / change_stage）

详见 `SLICE.md`。
"""

from hunter1.slices.applications.router import build_router
from hunter1.slices.applications.schemas import (
    ApplicationListResponse,
    ApplicationSummary,
    ApplyResponse,
    CreateApplicationRequest,
    StageUpdateRequest,
    StageUpdateResponse,
)
from hunter1.slices.applications.service import apply_to_job, change_stage, new_application
from hunter1.slices.applications.store import ApplicationStore

__all__ = [
    "ApplicationListResponse",
    "ApplicationStore",
    "ApplicationSummary",
    "ApplyResponse",
    "CreateApplicationRequest",
    "StageUpdateRequest",
    "StageUpdateResponse",
    "apply_to_job",
    "build_router",
    "change_stage",
    "new_application",
]
