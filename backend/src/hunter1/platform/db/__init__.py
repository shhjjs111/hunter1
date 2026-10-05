"""基础设施 · 数据库层（SQLite）。"""

from __future__ import annotations

from hunter1.platform.db.applications import SqliteApplicationRepository
from hunter1.platform.db.conversations import (
    Conversation,
    SqliteConversationRepository,
)
from hunter1.platform.db.database import Database, DatabaseLocationError
from hunter1.platform.db.repository import (
    SqliteCompanyRepository,
    SqliteJobRepository,
)
from hunter1.platform.db.schema import (
    ApplicationRow,
    Base,
    CompanyRow,
    ConversationMessageRow,
    ConversationRow,
    JobRow,
    SettingRow,
)
from hunter1.platform.db.settings import SqliteSettingsRepository

__all__ = [
    "ApplicationRow",
    "Base",
    "CompanyRow",
    "Conversation",
    "ConversationMessageRow",
    "ConversationRow",
    "Database",
    "DatabaseLocationError",
    "JobRow",
    "SettingRow",
    "SqliteApplicationRepository",
    "SqliteCompanyRepository",
    "SqliteConversationRepository",
    "SqliteJobRepository",
    "SqliteSettingsRepository",
]
