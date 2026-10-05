"""SQLite 数据库装配：连接、建表、会话与仓储工厂。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

from hunter1.infrastructure.db.conversations import SqliteConversationRepository
from hunter1.infrastructure.db.repository import (
    SqliteCompanyRepository,
    SqliteJobRepository,
)
from hunter1.infrastructure.db.schema import Base


class Database:
    """一个 SQLite 数据库的入口。只负责装配，不含业务规则。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(f"sqlite:///{self.path}", future=True)
        _enable_sqlite_pragmas(self.engine)

    def initialize(self) -> None:
        """建表（幂等）。"""
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = Session(self.engine, future=True)
        try:
            yield session
        finally:
            session.close()

    def companies(self) -> SqliteCompanyRepository:
        return SqliteCompanyRepository(self)

    def jobs(self) -> SqliteJobRepository:
        return SqliteJobRepository(self)

    def conversations(self) -> SqliteConversationRepository:
        return SqliteConversationRepository(self)

    def dispose(self) -> None:
        self.engine.dispose()


def _enable_sqlite_pragmas(engine: Engine) -> None:
    """开启 WAL（并发读写）与外键约束；SQLite 默认不检查外键。"""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


__all__ = ["Database"]
