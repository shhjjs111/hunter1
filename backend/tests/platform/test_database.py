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

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter1.domain.models import Job
from hunter1.platform.db import Database
from hunter1.platform.db.database import DatabaseLocationError


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
