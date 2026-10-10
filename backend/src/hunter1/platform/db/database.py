"""SQLite 数据库装配：连接、建表、会话与仓储工厂。"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from hunter1.platform.db.applications import SqliteApplicationRepository
from hunter1.platform.db.conversations import SqliteConversationRepository
from hunter1.platform.db.repository import (
    SqliteCompanyRepository,
    SqliteJobRepository,
)
from hunter1.platform.db.schema import Base
from hunter1.platform.db.settings import SqliteSettingsRepository

#: 写锁忙等待上限（毫秒）。显式设置，不依赖驱动隐式默认（5 秒）——
#: 抓取线程与 FastAPI 线程池并发写同一库时，5 秒一到就抛 `database is locked`。
BUSY_TIMEOUT_MS = 30_000


class DatabaseLocationError(RuntimeError):
    """数据库位置不可用（父路径是文件、指向目录、没有写权限、磁盘满……）。

    刻意用一个自有类型而不是把 `OSError` / `sqlalchemy.exc.OperationalError`
    直接往上抛：这类失败几乎总是**环境问题**（用户指错了路径、目录被组策略锁了），
    而不是程序缺陷。界面层据此给一句人话，而不是把 traceback 砸到用户脸上。
    """

    def __init__(self, path: Path, cause: OSError | None = None, *, reason: str = "") -> None:
        if reason:
            detail = reason
        elif cause is not None:
            detail = getattr(cause, "strerror", None) or str(cause)
        else:
            detail = "位置不可用"
        super().__init__(f"无法在 {path} 建库：{detail}")
        self.path = path
        self.cause = cause


class Database:
    """一个 SQLite 数据库的入口。只负责装配，不含业务规则。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.is_dir():
            # `--db` 指到一个目录：`create_engine` 不会立刻报错，直到 `initialize()`
            # 才抛 `OperationalError("unable to open database file")` —— 那不是
            # DatabaseLocationError，CLI 兜不住，用户看到的是裸 traceback。
            # 在构造期就翻译成同一种可读失败（判断成本一次 stat）。
            raise DatabaseLocationError(
                self.path, reason="这是一个目录，不是一个数据库文件（--db 要指向文件）"
            )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # 裸的 PermissionError / FileExistsError 对用户毫无意义 ——
            # 他不知道是哪个路径、也不知道该改什么。
            raise DatabaseLocationError(self.path, exc) from exc
        # 用 `URL.create` 而不是 f-string 拼 `sqlite:///{path}`：字符串 URL 会让
        # SQLAlchemy 对 database 分量做 percent-decode —— 路径里的 `%20` 会被解成
        # 空格、`%2F` 解成 `/`（实测：`D:/bak%20up/hunter1.db` → `D:/bak up/hunter1.db`）。
        # 于是上面 mkdir 字面路径、下面却打开另一个文件：可能开到一个不相干的库，
        # 也可能因父目录不存在而把「URL 解码」这层真实原因掩盖成 DatabaseLocationError。
        # `URL.create` 不做解码，字面路径与打开的文件始终是同一个。
        self.engine: Engine = create_engine(
            URL.create("sqlite", database=str(self.path)), future=True
        )
        _enable_sqlite_pragmas(self.engine)

    def initialize(self) -> None:
        """建表（幂等）+ 补写无法由 create_all 施加的唯一索引。"""
        try:
            self._initialize_schema()
        except DatabaseError as exc:
            # 连接/建表期的 DBAPI 失败同样是**环境问题**：库文件不可读、被别的进程
            # 独占锁着、磁盘满、或根本不是 SQLite 文件（实测：后者抛的是
            # `DatabaseError("file is not a database")`，**不是** OperationalError）。
            # 翻译成人话，让 CLI 的 `except DatabaseLocationError` 接得住 ——
            # 否则用户看到的是一段 traceback。原始信息一并带上，免得把实现 bug
            # 也伪装成环境问题。
            raise DatabaseLocationError(
                self.path, reason=str(getattr(exc, "orig", None) or exc)
            ) from exc

    def _initialize_schema(self) -> None:
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
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_conv_messages_conv_seq "
                    "ON conversation_messages (conversation_id, sequence)"
                )
            )
            # 清掉被取代的旧非唯一索引（旧代码建在 ORM 的 __table_args__ 里；升级后
            # 它与上面的唯一索引同列冗余 —— 多一份索引既占空间也拖慢写入，且两个索引
            # 描述同一组列会让读代码的人困惑）。IF EXISTS 兼顾从未建过它的库。
            connection.execute(text("DROP INDEX IF EXISTS ix_conv_messages_conv_seq"))

            # 报「已重排并补建唯一索引」必须放在**索引真的建成之后**。提前打印的话，
            # 万一建索引失败，日志已经宣称成功了 —— 而这条日志正是将来排查
            # 「消息顺序怎么变了」的唯一线索，说假话比不说更坏。
            if repaired:
                print(
                    f"检测到 {repaired} 个会话的消息序号重复，已重排并补建唯一索引。",
                    file=sys.stderr,
                )

            # 「一个岗位至多一条投递」同样必须由数据库保证（`applications/service.py`
            # 的先读后写在并发下失效：两个请求都读到空、各插一条）。老库里可能已经
            # 存在这种竞态留下的重复行 —— 先收敛再建索引，否则启动路径直接抛裸
            # IntegrityError、应用起不来（与上面消息序号同款处理）。
            removed = self._collapse_duplicate_applications(connection)
            if removed:
                print(
                    f"检测到 {removed} 条同一岗位的重复投递记录，已保留各岗位最新的一条"
                    "并补建唯一索引。",
                    file=sys.stderr,
                )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_applications_job ON applications (job_id)"
                )
            )
            # 清掉被取代的旧非唯一索引（旧代码建在 ORM 的 index=True 上）——
            # 与上面的消息索引同因：同列冗余只占空间、拖慢写入，还让读代码的人困惑。
            connection.execute(text("DROP INDEX IF EXISTS ix_applications_job_id"))

            # 新列：`create_all` 只对**新表**生效，老库补不上列（与上面的索引同因）。
            # SQLite 的 ADD COLUMN 没有 IF NOT EXISTS，先查 PRAGMA 再补。
            self._add_missing_columns(connection)

    @staticmethod
    def _add_missing_columns(connection: Connection) -> list[str]:
        """给已存在的表补上后加的列，返回补了哪些（`表.列`）。

        与索引那两处同一个理由：`create_all` 不会给老表加列，而应用启动后就会
        `SELECT` 新列 —— 不补会直接报 `no such column`，且用户无从自救。

        增删列时**同时**改这里与 `schema.py`：以 DDL 文本为准（SQLAlchemy 的一次性
        迁移工具对本项目是过度工程）。
        """
        wanted: dict[str, dict[str, str]] = {
            "jobs": {
                # 评分溯源（见 schema.py 的 JobRow）
                "score_model": "VARCHAR(128)",
                "score_prompt_version": "VARCHAR(64)",
                "scored_at": "DATETIME",
            },
        }
        added: list[str] = []
        inspector = inspect(connection)
        for table, columns in wanted.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name in existing:
                    continue
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                added.append(f"{table}.{name}")
        return added

    @staticmethod
    def _collapse_duplicate_applications(connection: Connection) -> int:
        """同一岗位有多条投递时只留最新那条，返回删除的行数。

        这些重复行只可能来自旧版的先读后写竞态，内容几乎相同（同一岗位、同一时刻
        的两条投递）。保留 `(updated_at DESC, id ASC)` 的第一条 = 更新的那条，
        并列时取 id 小的 —— 排序键确定，结果可复现。

        删除是**改用户数据**，不能静默：调用处把行数打到 stderr（与消息序号重排同款）。
        只处理确有重复的岗位（干净库上这条查询只做一次 GROUP BY）。
        """
        duplicated = (
            connection.execute(
                text("SELECT job_id FROM applications GROUP BY job_id HAVING COUNT(*) > 1")
            )
            .scalars()
            .all()
        )
        removed = 0
        for job_id in duplicated:
            rows = (
                connection.execute(
                    text(
                        "SELECT id FROM applications WHERE job_id = :job "
                        "ORDER BY updated_at DESC, id ASC"
                    ),
                    {"job": job_id},
                )
                .scalars()
                .all()
            )
            for application_id in rows[1:]:
                connection.execute(
                    text("DELETE FROM applications WHERE id = :id"), {"id": application_id}
                )
                removed += 1
        return removed

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
    """开启 WAL（并发读写）、外键约束与显式的忙等待上限；SQLite 默认不检查外键。

    `busy_timeout` 此前**从未显式设置**，靠 `sqlite3.connect` 的隐式默认（5 秒）。
    抓取线程与 FastAPI 的线程池会并发写同一个库：5 秒一到就抛 `database is
    locked`，而这是运行期错误、不在 `initialize()` 的 `except DatabaseError`
    覆盖内 —— 用户看到的是一段 traceback。显式放宽到 30 秒，让并发写去排队而不是
    直接失败（进程内的写窗口通常是毫秒级，30 秒只是「异常慢但仍能成功」的余量）。
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()


__all__ = ["Database", "DatabaseLocationError"]
