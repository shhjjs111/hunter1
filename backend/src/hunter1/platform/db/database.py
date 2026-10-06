"""SQLite 数据库装配：连接、建表、会话与仓储工厂。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session

from hunter1.platform.db.applications import SqliteApplicationRepository
from hunter1.platform.db.conversations import SqliteConversationRepository
from hunter1.platform.db.repository import (
    SqliteCompanyRepository,
    SqliteJobRepository,
)
from hunter1.platform.db.schema import Base
from hunter1.platform.db.settings import SqliteSettingsRepository


class DatabaseLocationError(RuntimeError):
    """数据库位置不可用（父路径是文件、没有写权限、磁盘满……）。

    刻意用一个自有类型而不是把 `OSError` 直接往上抛：这类失败几乎总是
    **环境问题**（用户指错了路径、目录被组策略锁了），而不是程序缺陷。
    界面层据此给一句人话，而不是把 traceback 砸到用户脸上。
    """

    def __init__(self, path: Path, cause: OSError) -> None:
        super().__init__(f"无法在 {path} 建库：{cause.strerror or cause}")
        self.path = path
        self.cause = cause


class Database:
    """一个 SQLite 数据库的入口。只负责装配，不含业务规则。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # 裸的 PermissionError / FileExistsError 对用户毫无意义 ——
            # 他不知道是哪个路径、也不知道该改什么。
            raise DatabaseLocationError(self.path, exc) from exc
        self.engine: Engine = create_engine(f"sqlite:///{self.path}", future=True)
        _enable_sqlite_pragmas(self.engine)

    def initialize(self) -> None:
        """建表（幂等）+ 补写无法由 create_all 施加的唯一索引。"""
        Base.metadata.create_all(self.engine)
        # (conversation_id, sequence) 必须唯一：并发 `append` 各算一次 `max+1` 会撞号，
        # 撞号必须被数据库拒绝（写入侧据此重试），否则两条同号消息静默入库、读回
        # 顺序错乱。放在这里而不是模型里 —— `create_all` 只对**新表**生效，老库补不上
        # 约束，而这条守卫对老库同样必要。`IF NOT EXISTS` 让本语句幂等。
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_conv_messages_conv_seq "
                    "ON conversation_messages (conversation_id, sequence)"
                )
            )

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

    def applications(self) -> SqliteApplicationRepository:
        return SqliteApplicationRepository(self)

    def settings(self) -> SqliteSettingsRepository:
        return SqliteSettingsRepository(self)

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


__all__ = ["Database", "DatabaseLocationError"]
