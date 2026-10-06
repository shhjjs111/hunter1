"""scoring 切片持久化门面测试 —— 真 SQLite，全程离线。

重点锁「分数不会被抓取线程的整行写静默回滚」这条不变量（见 TestScoreIsolation）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hunter1.domain.models import Job
from hunter1.platform.db import Database
from hunter1.slices.scoring.store import ScoreStore

JOB_ID = "b" * 32


def _job(**overrides: object) -> Job:
    base: dict[str, object] = {
        "id": JOB_ID,
        "company_id": "c1",
        "title": "AI产品经理",
        "detail_url": "https://example.com/job/1",
        "source": "offerbiu",
        "company_name": "字节跳动",
        "city": "北京",
    }
    base.update(overrides)
    return Job(**base)


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "scoring-store.db")
    database.initialize()
    return database


@pytest.fixture()
def store(db: Database) -> ScoreStore:
    return ScoreStore(db)


class TestSaveScore:
    def test_persists_score(self, store: ScoreStore, db: Database) -> None:
        db.jobs().upsert(_job())
        updated = store.save_score(JOB_ID, 80)
        assert updated is not None and updated.match_score == 80
        assert db.jobs().get(JOB_ID).match_score == 80  # type: ignore[union-attr]

    def test_missing_job_returns_none(self, store: ScoreStore) -> None:
        assert store.save_score("ghost", 80) is None

    def test_out_of_range_score_is_rejected(self, store: ScoreStore, db: Database) -> None:
        """定向列写绕过了 pydantic 约束，写库前必须显式兜住取值域。"""
        db.jobs().upsert(_job())
        with pytest.raises(ValueError):
            store.save_score(JOB_ID, 200)
        assert db.jobs().get(JOB_ID).match_score is None  # type: ignore[union-attr]

    def test_does_not_clobber_other_columns(self, store: ScoreStore, db: Database) -> None:
        """只写 match_score 一列 —— 抓取并发更新的标题/城市不被回滚。"""
        db.jobs().upsert(_job(title="原标题"))
        db.jobs().upsert_facts(_job(title="抓取更新的标题"))
        store.save_score(JOB_ID, 90)
        loaded = db.jobs().get(JOB_ID)
        assert loaded is not None
        assert loaded.title == "抓取更新的标题"
        assert loaded.match_score == 90


class TestScoreIsolation:
    def test_score_survives_concurrent_crawl_rewrite(self, store: ScoreStore, db: Database) -> None:
        """复现报告里的交错：crawl 读旧快照 → scoring 写分 → crawl 回写旧快照。

        修复前 crawl 的整行写会把刚打的 80 覆盖回 None；修复后抓取路径只写事实列，
        分数保持 80。
        """
        db.jobs().upsert(_job())
        stale = db.jobs().get(JOB_ID)  # crawl 在评分之前读到的快照（untouched）
        assert stale is not None

        store.save_score(JOB_ID, 80)  # scoring 写分

        # crawl 用它那份旧快照回写（stale.match_score 仍是 None）
        db.jobs().upsert_facts(stale)

        assert db.jobs().get(JOB_ID).match_score == 80  # type: ignore[union-attr]
