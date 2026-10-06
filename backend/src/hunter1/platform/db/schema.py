"""SQLite 表结构（SQLAlchemy 2.0 声明式）。

设计说明：
- `UtcDateTime` 保证时间字段往返始终是 **UTC aware**——SQLite 无原生时区，
  若不处理，读回来会变成 naive datetime，破坏模型相等性与比较。
- 仅存「领域字段」；`title_key` 冗余存储以便索引（同题折叠查询）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UtcDateTime(TypeDecorator):
    """始终以 UTC 读写 datetime（bind 时归一到 UTC，result 时补 tzinfo）。"""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    """所有表的基类。"""


class CompanyRow(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    campus_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    crawler_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    title_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    detail_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jd_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capture_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    first_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True, index=True)


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"
    # 注：(conversation_id, sequence) 的唯一性由 `Database.initialize()` 建的唯一索引
    # 强制，刻意不写在这里 —— `create_all` 只对**新表**生效，老库的约束不会被补上，
    # 而并发 append 恰恰在老库上也要能撞号（详见 database.py 的说明）。
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class ApplicationRow(Base):
    __tablename__ = "applications"
    # 注：`job_id` **刻意不加外键**，且 `company` / `title` 是投递那一刻从岗位
    # **复制**下来的快照（见 applications/service.py 的 `company=…, title=…`）。
    #
    # 理由：投递记录是「我申请了什么」的历史事实，不该随岗位库变动。
    # 若改成 `ForeignKey("jobs.id", ondelete="CASCADE")`，删掉一个岗位就会连带
    # 删掉投递记录 —— 那是数据丢失，不是完整性修复。SET NULL 也不对：快照字段
    # 本来就是为「岗位已不存在」准备的。
    #
    # 所以 `job_id` 只是个**弱引用**（便于回溯原岗位），不是完整性约束。
    # 改这里之前先想清楚：你要的是「投递历史」还是「岗位的附属记录」。
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="applied")
    applied_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class SettingRow(Base):
    """通用键值配置。

    用一张键值表而不是「每个设置一张表」：设置项会随版本增减（新增一个厂商
    预设、加一个界面偏好），键值表不必每次都改 schema。
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


__all__ = [
    "ApplicationRow",
    "Base",
    "CompanyRow",
    "ConversationMessageRow",
    "ConversationRow",
    "JobRow",
    "SettingRow",
    "UtcDateTime",
]
