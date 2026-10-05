"""Web 装配上下文 —— 把「界面需要的能力」收成一处，便于注入假实现。

界面层（web）是**组装处**：它认识基础设施（SQLite / HTTP 抓取器 / 模型客户端），
负责把它们接到应用层用例上。这样应用层仍然只依赖端口，测试可以完全离线。

测试要注入假模型、假抓取器，只需要换一个 `AppContext`，不必改任何路由。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hunter1.application.ports import Crawler, LLMProvider, TextFetcher
from hunter1.crawlers.registry import build_all
from hunter1.domain.settings import LLMSettings
from hunter1.platform.db import Database


def _now() -> datetime:
    return datetime.now(UTC)


def _default_llm_factory(settings: LLMSettings) -> LLMProvider:
    """按配置构造 OpenAI 兼容客户端。

    延迟导入：不装 `web` 之外的依赖时，只要不碰这个函数就不必付出导入成本。
    """
    from hunter1.platform.llm import OpenAICompatibleClient

    return OpenAICompatibleClient(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        timeout=60.0,
    )


@dataclass
class AppContext:
    """界面层依赖的一组能力。"""

    db: Database
    fetcher: TextFetcher
    llm_factory: Callable[[LLMSettings], LLMProvider] = _default_llm_factory
    clock: Callable[[], datetime] = _now
    site_keys: list[str] | None = None
    assistant_history_limit: int = 20
    page_size: int = 20

    def crawler_factory(self) -> list[Crawler]:
        """构造本轮要跑的抓取器（默认全部注册站点）。"""
        return build_all(fetcher=self.fetcher, keys=self.site_keys)

    @classmethod
    def default(
        cls,
        *,
        db_path: str | Path,
        site_keys: list[str] | None = None,
        fetcher: TextFetcher | None = None,
    ) -> AppContext:
        """按本机默认配置装配（真实 SQLite + 真实 HTTP 抓取器）。"""
        database = Database(db_path)
        database.initialize()
        if fetcher is None:
            from hunter1.platform.fetch.http import HttpFetcher

            fetcher = HttpFetcher(timeout=20, retries=2)
        return cls(db=database, fetcher=fetcher, site_keys=site_keys)


__all__ = ["AppContext"]
