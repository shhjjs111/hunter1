"""端口定义 —— 应用层依赖的抽象（依赖倒置的「接口」侧）。

应用层只依赖这些 Protocol，不依赖任何具体实现；基础设施层提供实现，
由组装处（interface 层）注入。这样 application 的测试可用内存假实现，
无需数据库。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter1.domain.models import Company, Job


@runtime_checkable
class CompanyRepository(Protocol):
    """公司仓储。"""

    def upsert(self, company: Company) -> None: ...

    def get(self, company_id: str) -> Company | None: ...

    def count(self) -> int: ...


@runtime_checkable
class JobRepository(Protocol):
    """岗位仓储。"""

    def upsert(self, job: Job) -> None: ...

    def get(self, job_id: str) -> Job | None: ...

    def count(self, *, company_id: str | None = None) -> int: ...

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Job]: ...


__all__ = ["CompanyRepository", "JobRepository"]
