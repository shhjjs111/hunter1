"""Web 测试的共享替身与工具函数。

放在这里而不是各个测试文件里，是为了让「模型替身」只有一个来源 ——
非流式与流式两条路径必须给出一致行为，两份 `FakeLLM` 迟早会漂移。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter1.domain.llm import LLMResponse, StreamComplete, TextDelta
from hunter1.domain.models import CaptureStatus, Job
from hunter1.domain.settings import LLMSettings
from hunter1.infrastructure.db import Database

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parent.parent / "crawlers" / "fixtures"


class FakeFetcher:
    """任何 URL 都返回同一个页面快照 —— 让抓取链路可以离线跑通。"""

    def __init__(self, html: str) -> None:
        self.html = html
        self.requested: list[str] = []

    def get_text(self, url: str, **_kwargs: object) -> str:
        self.requested.append(url)
        return self.html


class FakeLLM:
    """按脚本作答的假模型。

    - `reply=None` → 调用时报错，用于验证失败路径；
    - `script` 给了就按脚本产出事件（用于流式的工具调用场景）；
    - 否则把 `reply` 按 `chunk_size` 切片，模拟逐字输出。

    非流式与流式都实现，且语义一致 —— 界面在「逐字输出」与「无 JS 回退」
    两条路上不该表现出不同的行为。
    """

    def __init__(
        self,
        reply: str | None = "好的，共 2 条岗位。",
        *,
        chunk_size: int = 4,
        script: list[list[Any]] | None = None,
    ) -> None:
        self.reply = reply
        self.chunk_size = max(1, chunk_size)
        self.script = list(script) if script is not None else None
        self.seen_messages: list[Any] = []
        self.stream_calls = 0

    # ---- 非流式 ----

    def complete(
        self, *, system_prompt: str, user_prompt: str, max_tokens: int | None = None
    ) -> LLMResponse:
        if self.reply is None:
            raise RuntimeError("模型不可用")
        return LLMResponse(content=self.reply, model="fake-model")

    def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, object],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return self.complete(system_prompt=system_prompt, user_prompt=user_prompt)

    def complete_with_tools(
        self,
        *,
        messages: list[object],
        tools: list[dict[str, object]],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.seen_messages = list(messages)
        if self.reply is None:
            raise RuntimeError("模型不可用")
        return LLMResponse(content=self.reply, model="fake-model")

    # ---- 流式 ----

    def stream_with_tools(
        self,
        *,
        messages: list[object],
        tools: list[dict[str, object]],
        max_tokens: int | None = None,
    ) -> Any:
        self.stream_calls += 1
        self.seen_messages = list(messages)
        if self.script is not None:
            yield from (self.script.pop(0) if self.script else [])
            return
        if self.reply is None:
            raise RuntimeError("模型不可用")
        for start in range(0, len(self.reply), self.chunk_size):
            yield TextDelta(self.reply[start : start + self.chunk_size])
        yield StreamComplete(content=self.reply, model="fake-model")


def seed_jobs(db: Database) -> None:
    """放两个岗位进去，供页面与工具测试使用。"""
    repo = db.jobs()
    repo.upsert(
        Job(
            id="j1" + "0" * 30,
            company_id="c1",
            title="AI产品经理",
            detail_url="https://a.com/1",
            source="实习僧",
            company_name="字节跳动",
            city="北京",
            match_score=88,
            capture_status=CaptureStatus.COMPLETE,
            last_seen_at=NOW,
        )
    )
    repo.upsert(
        Job(
            id="j2" + "0" * 30,
            company_id="c2",
            title="行政专员",
            detail_url="https://a.com/2",
            source="实习僧",
            company_name="某国企",
            last_seen_at=NOW,
        )
    )


def configure_llm(db: Database, *, key: str = "sk-test") -> None:
    db.settings().save_llm(
        LLMSettings(base_url="https://api.example.com/v1", model="m", api_key=key)
    )


def parse_sse(body: str) -> list[dict[str, Any]]:
    """把 `text/event-stream` 正文解析成事件列表（忽略注释与空块）。"""
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        payload = block[len("data:") :].strip()
        if not payload:
            continue
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


__all__ = [
    "FIXTURES",
    "NOW",
    "FakeFetcher",
    "FakeLLM",
    "configure_llm",
    "parse_sse",
    "seed_jobs",
]
