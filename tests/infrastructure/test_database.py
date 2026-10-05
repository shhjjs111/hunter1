"""数据库装配的跨线程可用性 —— 一条回归护栏。

背景：抓取在后台线程里跑（`web/crawl_runner.py` 起线程，线程内通过仓储写库），
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

from hunter1.domain.models import Job
from hunter1.infrastructure.db import Database


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
