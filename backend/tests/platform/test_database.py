"""数据库装配的跨线程可用性 —— 一条回归护栏。

背景：抓取在后台线程里跑（`slices/crawl/runner.py` 起线程，线程内通过仓储写库），
主线程同时可能在读页面。

一次外部审查把「`create_engine` 没显式传 `check_same_thread=False`」报成了缺陷。
**核验后不成立**：SQLAlchemy 2.x 的 pysqlite 方言对文件型 SQLite 已经默认
设为 `False`（本文件最后一个用例断言了这一点）。所以当前行为是正确的，
不需要「修复」。

那这个文件为什么还留着？因为这份正确性是**库的默认**给的，不是我们的代码写死的。
哪天有人换连接池、换 URL 形态、或换 SQLAlchemy 大版本，这份默认可能变 ——
而故障表现是「后台抓取偶发崩溃」，最难查的那一类。所以用测试把「多线程能写」
这件事钉住：换配置而没想清楚，这里会红。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from hunter1.domain.models import Job
from hunter1.platform.db import BUSY_TIMEOUT_MS, Database
from hunter1.platform.db.database import DatabaseLocationError, _restrict_permissions


def test_busy_timeout_pragma_is_explicit(tmp_path: Path) -> None:
    """`busy_timeout` 必须显式设置，不能靠驱动的隐式默认（5 秒）。

    抓取线程与 FastAPI 线程池并发写同一个库：隐式 5 秒一到就抛 `database is
    locked`，而那是运行期错误、不在 `initialize()` 的 `except DatabaseError`
    覆盖内（用户看到裸 traceback）。
    """
    from sqlalchemy import text

    db = Database(tmp_path / "pragmas.db")
    db.initialize()
    with db.engine.connect() as connection:
        timeout = connection.execute(text("PRAGMA busy_timeout")).scalar()
        journal = connection.execute(text("PRAGMA journal_mode")).scalar()
    assert timeout == BUSY_TIMEOUT_MS
    assert str(journal).lower() == "wal"
    db.dispose()


def test_write_transaction_takes_the_write_lock_up_front(tmp_path: Path) -> None:
    """写事务必须一开始就抢写锁，避免两个合并同时读旧快照后再升级冲突。"""
    from sqlalchemy import text

    db = Database(tmp_path / "write-lock.db")
    db.initialize()
    try:
        with db.transaction() as session:
            session.execute(text("SELECT 1"))
            with db.engine.connect() as other:
                other.exec_driver_sql("PRAGMA busy_timeout=50")
                with pytest.raises(OperationalError, match="locked"):
                    other.exec_driver_sql("BEGIN IMMEDIATE")
    finally:
        db.dispose()


def test_foreign_keys_pragma_is_on(tmp_path: Path) -> None:
    """外键开关必须显式打开（SQLite 默认**不检查**外键）。

    当前 schema 刻意不建外键（`applications` 是岗位快照，加 CASCADE 会在删岗位时
    连带删掉投递历史 —— 那条有专门的用例），所以现在删掉这一行**没有任何行为
    差异**：实测删掉后 14 条 database 用例全绿。正因为「无行为差异」，它只能靠直接
    问 pragma 来钉 —— 将来任何一张表加外键时，这行是唯一的开关。
    """
    from sqlalchemy import text

    db = Database(tmp_path / "fk.db")
    db.initialize()
    with db.engine.connect() as connection:
        enabled = connection.execute(text("PRAGMA foreign_keys")).scalar()
    assert enabled == 1
    db.dispose()


def _job(index: int) -> Job:
    return Job(
        id=f"j{index}",
        company_id="c1",
        title=f"岗位{index}",
        detail_url=f"https://a.com/{index}",
        source="t",
        first_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


class TestUnusableLocation:
    """路径不可用时给人话，而不是裸 `OSError`。

    这类失败几乎总是环境问题（指错路径、目录被组策略锁、磁盘满），
    不是程序缺陷 —— 用户需要的是「哪个路径、为什么」，不是 traceback。
    """

    def test_parent_is_a_file(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("我是文件不是目录", encoding="utf-8")
        with pytest.raises(DatabaseLocationError) as excinfo:
            Database(blocker / "sub" / "hunter1.db")
        message = str(excinfo.value)
        assert "hunter1.db" in message  # 报出是哪个路径
        assert message.strip()

    def test_error_carries_path_and_cause(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        target = blocker / "sub" / "hunter1.db"
        with pytest.raises(DatabaseLocationError) as excinfo:
            Database(target)
        assert excinfo.value.path == target
        assert isinstance(excinfo.value.cause, OSError)

    def test_is_not_a_bare_oserror(self, tmp_path: Path) -> None:
        """必须是自有类型 —— 界面层靠它区分「环境问题」与「程序缺陷」。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        with pytest.raises(DatabaseLocationError):
            Database(blocker / "hunter1.db")

    def test_usable_nested_path_still_works(self, tmp_path: Path) -> None:
        """别把正常的深层路径误判成错误。"""
        db = Database(tmp_path / "a" / "b" / "c" / "hunter1.db")
        db.initialize()
        assert db.path.parent.is_dir()

    def test_percent_escapes_in_path_are_not_decoded(self, tmp_path: Path) -> None:
        """含 `%xx` 的路径必须打开**同一个**文件，不能被 SQLAlchemy 解成别的路径。

        用 f-string 拼 `sqlite:///{path}` 时，SQLAlchemy 会对 database 分量做
        percent-decode（实测：`D:/bak%20up/hunter1.db` → `D:/bak up/hunter1.db`）。
        于是 mkdir 作用于字面路径、SQLite 却打开另一个文件。改用 `URL.create` 后
        两者一致 —— 这里钉住它。
        """
        target = tmp_path / "bak%20up" / "hunter1.db"
        db = Database(target)
        db.initialize()
        assert target.is_file(), "数据库应落在字面路径上"
        # 对照：被解码后的那个路径不该出现
        assert not (tmp_path / "bak up" / "hunter1.db").exists()

    def test_slash_escape_in_path_is_not_decoded(self, tmp_path: Path) -> None:
        """`%2F` 若被解码会变成路径分隔符，落到另一层目录里。"""
        target = tmp_path / "x%2Fy" / "hunter1.db"
        db = Database(target)
        db.initialize()
        assert target.is_file()
        assert not (tmp_path / "x" / "y" / "hunter1.db").exists()

    def test_path_pointing_at_a_directory_is_rejected_readably(self, tmp_path: Path) -> None:
        """`--db` 指到一个目录时给可读失败，而不是连库时才炸出一段 traceback。

        `create_engine` 对目录是**惰性失败**：直到 `initialize()` 才抛
        `OperationalError("unable to open database file")` —— 那不是
        DatabaseLocationError，CLI 兜不住。构造期就翻译成同一种可读失败。
        """
        target = tmp_path / "a-directory"
        target.mkdir()
        with pytest.raises(DatabaseLocationError) as excinfo:
            Database(target)
        assert "目录" in str(excinfo.value)
        assert excinfo.value.path == target

    def test_connection_failure_at_initialize_is_translated(self, tmp_path: Path) -> None:
        """连接/建表期失败同样要变成可读失败，而不是直穿成 traceback。

        实测：把一个非 SQLite 内容的文件放成库，抛的是
        `sqlalchemy.exc.DatabaseError("file is not a database")` ——
        **不是** OperationalError，只捕后者会漏。
        """
        path = tmp_path / "locked.db"
        database = Database(path)
        database.initialize()
        database.dispose()

        path.write_bytes(b"not a database" * 100)
        broken = Database(path)
        with pytest.raises(DatabaseLocationError) as excinfo:
            broken.initialize()
        assert "database" in str(excinfo.value).lower()


class TestCrossThreadWrites:
    def test_concurrent_writes_from_many_threads(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "threads.db")
        db.initialize()
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def worker(index: int) -> None:
            try:
                barrier.wait(timeout=10)  # 尽量让它们真正并发
                db.jobs().upsert(_job(index))
            except BaseException as exc:  # 收集起来在主线程断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == [], f"跨线程写库失败：{errors[:3]}"
        assert db.jobs().count() == 8

    def test_read_then_write_alternating_across_threads(self, tmp_path: Path) -> None:
        """读写交替最容易触发「连接被交给另一个线程」—— 池子会来回借还。"""
        db = Database(tmp_path / "alt.db")
        db.initialize()
        db.jobs().upsert(_job(999))  # 先有一个连接被创建并归还
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                db.jobs().count()
                db.jobs().upsert(_job(index))
                db.jobs().list(limit=5)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == [], f"跨线程读写失败：{errors[:3]}"
        assert db.jobs().count() == 7

    def test_engine_allows_cross_thread_connections(self, tmp_path: Path) -> None:
        """锁住「库替我们关掉了 check_same_thread」这个事实。

        不是我们在修什么 —— 是防止未来换连接配置时静默破坏后台抓取。
        """
        db = Database(tmp_path / "cfg.db")
        options = db.engine.dialect.create_connect_args(db.engine.url)[1]
        assert options.get("check_same_thread") is False


class TestColumnBackfill:
    """后加的列要能补到**老库**上。

    `create_all` 只对不存在的表生效 —— 老库不会被加列，而应用启动后就会 SELECT
    新列，结果是 `no such column`、应用起不来。与唯一索引那两处同一个理由。
    """

    def _drop_new_columns(self, db: Database) -> None:
        import sqlalchemy as sa

        with db.engine.begin() as conn:
            for name in ("score_model", "score_prompt_version", "scored_at"):
                conn.execute(sa.text(f"ALTER TABLE jobs DROP COLUMN {name}"))

    def test_old_database_gets_the_new_columns(self, tmp_path: Path) -> None:
        import sqlalchemy as sa

        path = tmp_path / "old.db"
        first = Database(path)
        first.initialize()
        self._drop_new_columns(first)
        first.dispose()

        reopened = Database(path)
        reopened.initialize()  # 修复前这里之后读 jobs 会炸

        with reopened.engine.connect() as conn:
            names = {column["name"] for column in sa.inspect(conn).get_columns("jobs")}
        assert {"score_model", "score_prompt_version", "scored_at"} <= names
        reopened.jobs().get("missing")  # 读一次确认 SELECT 不再报 no such column
        reopened.dispose()

    def test_backfill_is_idempotent(self, tmp_path: Path) -> None:
        """补过的库再 initialize 不该重复 ALTER（返回值即「这次补了什么」）。"""

        db = Database(tmp_path / "twice.db")
        db.initialize()
        with db.engine.begin() as conn:
            assert Database._add_missing_columns(conn) == []
        self._drop_new_columns(db)
        with db.engine.begin() as conn:
            added = Database._add_missing_columns(conn)
        assert sorted(added) == ["jobs.score_model", "jobs.score_prompt_version", "jobs.scored_at"]


class TestSchemaExports:
    """schema.py 的 `__all__` 只能有一处 —— 重复赋值会让后者静默覆盖前者。"""

    def test_all_is_assigned_exactly_once(self) -> None:
        """出现第二个顶层 __all__ 赋值时，往第一个里加的名字会被无声吞掉。"""
        import ast

        source = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "hunter1"
            / "platform"
            / "db"
            / "schema.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        assignments = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            )
        ]
        assert len(assignments) == 1, f"schema.py 有 {len(assignments)} 处顶层 __all__ 赋值"

    def test_exported_names_exist(self) -> None:
        from hunter1.platform.db import schema

        for name in schema.__all__:
            assert hasattr(schema, name), f"__all__ 里的 {name} 在模块中不存在"
        assert {"ApplicationRow", "Base", "CompanyRow", "JobRow", "UtcDateTime"} <= set(
            schema.__all__
        )


#: 造一个 SQLAlchemy 的 `OperationalError`（带 sqlite3 原始异常），
#: 用来模拟 `initialize()` 阶段的两类失败。
def _operational(message: str) -> OperationalError:
    return OperationalError("stmt", {}, sqlite3.OperationalError(message))


class TestInitializeFailureClassification:
    """`initialize()` 的失败分类：环境问题翻译成人话，结构问题原样抛。

    两者混为一类会把自救方向指错 —— CLI 照着「位置不可用」的话术让用户换
    `--db` 路径，而换路径对「补列撞名 / 建索引失败」毫无作用。
    """

    def test_schema_failure_is_not_masked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = Database(tmp_path / "schema-fail.db")

        def boom() -> None:
            raise _operational("duplicate column name: score_model")

        monkeypatch.setattr(db, "_initialize_schema", boom)
        with pytest.raises(OperationalError):
            db.initialize()

    def test_environment_failure_is_translated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = Database(tmp_path / "env-fail.db")

        def boom() -> None:
            raise _operational("unable to open database file")

        monkeypatch.setattr(db, "_initialize_schema", boom)
        with pytest.raises(DatabaseLocationError) as info:
            db.initialize()
        assert "unable to open database file" in str(info.value)

    def test_classification_is_what_makes_the_difference(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """对照：把分类判据关掉，同一条结构失败就**变回**被伪装的位置错误。

        这条是上面那条的「回滚验证」：它证明结果确实由分类逻辑决定，
        而不是碰巧（若哪天有人删掉分流，本用例会红）。
        """
        db = Database(tmp_path / "control.db")

        def boom() -> None:
            raise _operational("duplicate column name: score_model")

        monkeypatch.setattr(db, "_initialize_schema", boom)
        monkeypatch.setattr(
            "hunter1.platform.db.database._is_schema_failure", lambda _message: False
        )
        with pytest.raises(DatabaseLocationError):
            db.initialize()


class TestSchemaFailureHints:
    """反向枚举的安全性：环境错误的形态远多于结构错误，别误伤。"""

    @pytest.mark.parametrize(
        "message",
        [
            "unable to open database file",
            "attempt to write a readonly database",
            "database is locked",
            "database or disk is full",
            "file is not a database",
            "disk I/O error",
        ],
    )
    def test_environment_messages_are_not_classified_as_schema(self, message: str) -> None:
        from hunter1.platform.db.database import _is_schema_failure

        assert _is_schema_failure(message) is False

    @pytest.mark.parametrize(
        "message",
        ["duplicate column name: x", "no such column: y", "syntax error near SELECT"],
    )
    def test_schema_messages_are_classified(self, message: str) -> None:
        from hunter1.platform.db.database import _is_schema_failure

        assert _is_schema_failure(message) is True


class TestConcurrentInitialization:
    """并发 `initialize()` 同一个库（报告测试薄弱点）。

    「双进程同启一个库」是便携工具的真实场景：用户双击两次、或定时任务与手动启动
    重叠。建表 / 补列是**幂等**的，但并发下会撞「table already exists」——
    而 `initialize()` 的分类逻辑会把这类消息当成**结构性错误**报出来。
    并发路径此前只有串行测试覆盖，这里把它钉住：要么都成功，要么失败方式一致可读。
    """

    def test_four_threads_initialize_the_same_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "concurrent.db"
        errors: list[Exception] = []
        barrier = threading.Barrier(4, timeout=10)

        def initialize() -> None:
            database = Database(db_path)
            try:
                barrier.wait()  # 让四个线程尽可能同时进入
                database.initialize()
            except Exception as exc:
                errors.append(exc)
            finally:
                database.dispose()

        threads = [threading.Thread(target=initialize) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        assert not errors, f"并发 initialize 失败：{errors}"

        # 库确实建好了：再初始化一次应当无事发生
        check = Database(db_path)
        try:
            check.initialize()
        finally:
            check.dispose()


def test_posix_permissions_are_restricted_for_sensitive_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "hunter1.db"
    target.write_text("contains-api-key", encoding="utf-8")
    seen: list[tuple[Path, int]] = []

    def fake_chmod(path: Path, mode: int) -> None:
        seen.append((path, mode))

    monkeypatch.setattr(Path, "chmod", fake_chmod)
    _restrict_permissions(target, 0o600, posix=True)

    assert seen == [(target, 0o600)]


def test_permission_hardening_is_a_noop_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "hunter1.db"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        Path, "chmod", lambda *_args, **_kwargs: pytest.fail("Windows 分支不该 chmod")
    )
    _restrict_permissions(target, 0o600, posix=False)


def test_existing_database_permissions_are_tightened_before_schema_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hunter1.platform.db.database as database_module

    path = tmp_path / "existing.db"
    path.write_text("not a real database", encoding="utf-8")
    tightened: list[tuple[Path, int]] = []

    def record(target: Path, mode: int, *, posix: bool | None = None) -> None:
        tightened.append((target, mode))

    monkeypatch.setattr(database_module, "_restrict_permissions", record)
    database = Database(path)
    try:
        assert (path, 0o600) in tightened
    finally:
        database.dispose()
