"""端口定义 —— 应用层依赖的抽象（依赖倒置的「接口」侧）。

应用层只依赖这些 Protocol，不依赖任何具体实现；基础设施层提供实现，
由组装处（interface 层）注入。这样 application 的测试可用内存假实现，
无需数据库。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from hunter1.domain.assistant import Message
from hunter1.domain.crawl import RawJob
from hunter1.domain.llm import LLMResponse
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

    def search(self, *, keyword: str, limit: int = 20, offset: int = 0) -> list[Job]: ...


@runtime_checkable
class TextFetcher(Protocol):
    """能按 URL 取回文本的最小契约。

    适配器只依赖这个窄接口，因此测试可注入假实现（返回内联 HTML），
    无需真实网络；也不必依赖 httpx 的具体类型。
    """

    def get_text(self, url: str, *, headers: dict[str, str] | None = None) -> str: ...


@runtime_checkable
class Crawler(Protocol):
    """一个招聘站点的适配器。

    实现者只需把「某个公司的招聘页」翻成一批 `RawJob`；并发控制、重试、
    限流由注入的抓取基础设施负责，适配器不重复实现这些。
    """

    company: str
    careers_url: str

    def fetch(self) -> list[RawJob]: ...


@runtime_checkable
class LLMProvider(Protocol):
    """大模型能力的最小契约。

    应用层只依赖这几个方法，因此换厂商（OpenAI / DeepSeek / 通义 / 智谱 / 自建网关…）
    不需要改任何用例代码；测试可注入假实现，完全离线。
    """

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    def complete_with_tools(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


__all__ = [
    "CompanyRepository",
    "Crawler",
    "JobRepository",
    "LLMProvider",
    "TextFetcher",
]
