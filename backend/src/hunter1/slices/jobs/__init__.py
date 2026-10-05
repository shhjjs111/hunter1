"""jobs 切片 —— 岗位与公司的核心域（含投递入口）。

公开面（其他切片只允许从这里 import 符号）：

- `JobStore`：数据存取门面
- `build_router`：HTTP 面工厂（组装处注入依赖）
- `schemas`：API 模型（契约源头）
- `service`：用例函数（list_jobs / find_job / apply_to_job）

详见 `SLICE.md`。
"""

from hunter1.slices.jobs.router import build_router
from hunter1.slices.jobs.schemas import (
    ApplyResponse,
    JobDetail,
    JobListResponse,
    JobSummary,
)
from hunter1.slices.jobs.service import JobPage, apply_to_job, find_job, list_jobs
from hunter1.slices.jobs.store import JobStore

__all__ = [
    "ApplyResponse",
    "JobDetail",
    "JobListResponse",
    "JobPage",
    "JobStore",
    "JobSummary",
    "apply_to_job",
    "build_router",
    "find_job",
    "list_jobs",
]
