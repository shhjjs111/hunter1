"""jobs 切片 —— 岗位与公司的核心域。

公开面（其他切片只允许从这里 import 符号）：

- `JobStore`：数据存取门面
- `build_router`：HTTP 面工厂（组装处注入依赖）
- `schemas`：API 模型（契约源头）
- `service`：用例函数（list_jobs / find_job）

「记录投递」不在这里 —— 投递记录的本体归 applications 切片，入口随之归它。

详见 `SLICE.md`。
"""

from hunter1.slices.jobs.router import build_router
from hunter1.slices.jobs.schemas import (
    JobDetail,
    JobListResponse,
    JobSummary,
)
from hunter1.slices.jobs.service import JobPage, find_job, list_jobs
from hunter1.slices.jobs.store import JobStore

__all__ = [
    "JobDetail",
    "JobListResponse",
    "JobPage",
    "JobStore",
    "JobSummary",
    "build_router",
    "find_job",
    "list_jobs",
]
