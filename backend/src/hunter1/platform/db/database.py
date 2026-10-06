"""SQLite 数据库装配：连接、建表、会话与仓储工厂。"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
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
            # **先修脏数据再建索引**：索引落地前撞过号的老库会存在重复行，
            # 直接 `CREATE UNIQUE INDEX` 会抛裸 IntegrityError —— 而本方法在
            # `AppContext.default` 的启动路径上，后果是应用起不来、用户无从自救。
            # 重排（而非删除）能保住全部消息并恢复顺序。修复是在**改用户数据**，
            # 不能静默 —— 真触发了要有迹可循，否则将来排查「消息顺序怎么变了」
            # 时没有任何线索。
            repaired = self._repair_duplicate_message_sequences(connection)
            if repaired:
                print(
                    f"检测到 {repaired} 个会话的消息序号重复，已重排并补建唯一索引。",
                    file=sys.stderr,
                )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_conv_messages_conv_seq "
                    "ON conversation_messages (conversation_id, sequence)"
                )
            )

    @staticmethod
    def _repair_duplicate_message_sequences(connection: Connection) -> int:
        """把存在重复序号的会话整体重排为 1..N，为建唯一索引扫清障碍。

        只处理确有重复的会话（干净库上这条查询只做一次 GROUP BY），避免无谓写入。
        排序键用 (sequence, created_at, id)：先按原序号，再按时间与 id 稳定化 ——
        保证重排结果确定，且大体保持原有先后顺序。

        返回被修复的会话数（0 = 干净库，无副作用）。
        """
        duplicated = (
            connection.execute(
                text(
                    "SELECT conversation_id FROM conversation_messages "
                    "GROUP BY conversation_id, sequence HAVING COUNT(*) > 1"
                )
            )
            .scalars()
            .all()
        )
        conversation_ids = set(duplicated)
        for conversation_id in conversation_ids:
            message_ids = (
                connection.execute(
                    text(
                        "SELECT id FROM conversation_messages WHERE conversation_id = :cid "
                        "ORDER BY sequence, created_at, id"
                    ),
                    {"cid": conversation_id},
                )
                .scalars()
                .all()
            )
            for sequence, message_id in enumerate(message_ids, start=1):
                connection.execute(
                    text("UPDATE conversation_messages SET sequence = :seq WHERE id = :mid"),
                    {"seq": sequence, "mid": message_id},
                )
        return len(conversation_ids)

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
