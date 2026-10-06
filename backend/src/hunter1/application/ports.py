"""端口定义 —— 应用层依赖的抽象（依赖倒置的「接口」侧）。

应用层只依赖这些 Protocol，不依赖任何具体实现；基础设施层提供实现，
由组装处（interface 层）注入。这样 application 的测试可用内存假实现，
无需数据库。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable

from hunter1.domain.assistant import Message
from hunter1.domain.crawl import RawJob
from hunter1.domain.llm import LLMResponse, StreamComplete, TextDelta
from hunter1.domain.models import Application, Company, Job
from hunter1.domain.settings import LLMSettings


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

    def get_by_prefix(self, prefix: str) -> list[Job]: ...

    def count(self, *, company_id: str | None = None, keyword: str | None = None) -> int: ...

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Job]: ...

    def search(self, *, keyword: str, limit: int = 20, offset: int = 0) -> list[Job]: ...


@runtime_checkable
class ApplicationRepository(Protocol):
    """投递记录仓储。"""

    def upsert(self, application: Application) -> None: ...

    def get(self, application_id: str) -> Application | None: ...

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Application]: ...

    def by_job(self, job_id: str) -> list[Application]: ...

    def count(self) -> int: ...

    def delete(self, application_id: str) -> None: ...


@runtime_checkable
class SettingsRepository(Protocol):
    """配置仓储（当前只有 LLM 配置）。"""

    def get_llm(self) -> LLMSettings | None: ...

    def save_llm(self, settings: LLMSettings) -> None: ...


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

    `key` 是适配器的**唯一标识**（站点注册表的 key），进度关联用它；
    `company` 是给人看的显示名，可能两个站点同名 —— 不用作关联键。
    """

    key: str
    company: str
    careers_url: str

    def fetch(self) -> list[RawJob]: ...


class ModelNotConfiguredError(RuntimeError):
    """组装处拿不到可用的模型配置，因而无法构造 `LLMProvider`。

    这**不是服务端故障**，而是初始状态（用户还没在「配置」页填写
    base_url / 模型 / API Key）。声明在进程边界模块里，是为了让
    「构造 provider 可能以这种方式失败」成为**契约的一部分**：
    组装处与各切片据此给出可行动的错误，而不是让一个 `RuntimeError`
    穿透成 500「Internal Server Error」—— 后者对用户零信息量。
    """


@runtime_checkable
class LLMProvider(Protocol):
    """大模型能力的最小契约。

    应用层只依赖这几个方法，因此换厂商（OpenAI / DeepSeek / 通义 / 智谱 / 自建网关…）
    不需要改任何用例代码；测试可注入假实现，完全离线。

    `stream_with_tools` 的承诺是**始终可用**：厂商不支持流式时由实现自行退化
    （见 `platform.llm`），调用方不必判断「这家支不支持流式」。
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

    def stream_with_tools(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> Iterator[TextDelta | StreamComplete]: ...


__all__ = [
    "ApplicationRepository",
    "CompanyRepository",
    "Crawler",
    "JobRepository",
    "LLMProvider",
    "ModelNotConfiguredError",
    "SettingsRepository",
    "TextFetcher",
]
