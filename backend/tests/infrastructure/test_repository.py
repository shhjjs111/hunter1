"""仓储层单元测试 —— 临时 SQLite 文件，验证持久化往返、幂等与不变量。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter1.domain.models import CaptureStatus, Company, Job
from hunter1.infrastructure.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "hunter1.db")
    database.initialize()
    return database


def _job(**overrides: object) -> Job:
    base: dict[str, object] = {
        "id": "j1",
        "company_id": "c1",
        "title": "AI产品经理（2027校招）",
        "detail_url": "https://example.com/job/1",
        "source": "offerbiu",
    }
    base.update(overrides)
    return Job(**base)


class TestCompanies:
    def test_upsert_then_get_roundtrip(self, db: Database) -> None:
        repo = db.companies()
        company = Company(id="c1", name="字节跳动", source="offerbiu", aliases=["ByteDance"])
        repo.upsert(company)
        assert repo.get("c1") == company

    def test_upsert_updates_in_place(self, db: Database) -> None:
        repo = db.companies()
        repo.upsert(Company(id="c1", name="旧名", source="offerbiu"))
        repo.upsert(Company(id="c1", name="新名", source="offerbiu"))
        assert repo.get("c1") is not None
        assert repo.get("c1").name == "新名"  # type: ignore[union-attr]
        assert repo.count() == 1

    def test_get_missing_returns_none(self, db: Database) -> None:
        assert db.companies().get("missing") is None


class TestJobs:
    def test_roundtrip_preserves_all_fields(self, db: Database) -> None:
        repo = db.jobs()
        job = _job(
            match_score=88,
            capture_status=CaptureStatus.COMPLETE,
            city="北京",
            company_name="字节跳动",
            jd_raw="岗位描述正文",
            first_seen_at=datetime(2026, 9, 1, tzinfo=UTC),
            last_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        repo.upsert(job)
        loaded = repo.get("j1")
        assert loaded == job
        # 归一化投影在往返后仍一致（保证同题折叠稳定）
        assert loaded is not None and loaded.title_key == "ai产品经理"

    def test_upsert_is_idempotent(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job())
        repo.upsert(_job())
        assert repo.count() == 1

    def test_upsert_updates_existing_row(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(match_score=10))
        repo.upsert(_job(match_score=90))
        loaded = repo.get("j1")
        assert loaded is not None and loaded.match_score == 90
        assert repo.count() == 1

    def test_count_by_company(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="j1", company_id="c1"))
        repo.upsert(_job(id="j2", company_id="c1"))
        repo.upsert(_job(id="j3", company_id="c2"))
        assert repo.count() == 3
        assert repo.count(company_id="c1") == 2

    def test_count_by_keyword_matches_search(self, db: Database) -> None:
        """分页要显示「共几页」，所以计数必须和搜索命中同一批记录。"""
        repo = db.jobs()
        repo.upsert(_job(id="j1", title="AI产品经理"))
        repo.upsert(_job(id="j2", title="数据产品经理（2027校招）"))
        repo.upsert(_job(id="j3", title="行政专员"))
        assert repo.count(keyword="产品经理") == 2
        assert repo.count(keyword="产品经理") == len(repo.search(keyword="产品经理", limit=100))

    def test_count_by_keyword_no_match(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="j1", title="AI产品经理"))
        assert repo.count(keyword="律师") == 0

    def test_count_with_empty_keyword_is_total(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="j1", title="AI产品经理"))
        assert repo.count(keyword="") == 1

    def test_list_orders_by_last_seen_desc_nulls_last(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="old", last_seen_at=datetime(2026, 9, 1, tzinfo=UTC)))
        repo.upsert(_job(id="new", last_seen_at=datetime(2026, 10, 1, tzinfo=UTC)))
        repo.upsert(_job(id="never"))  # last_seen_at = None
        assert [j.id for j in repo.list(limit=10)] == ["new", "old", "never"]

    def test_list_pagination(self, db: Database) -> None:
        repo = db.jobs()
        for index in range(5):
            repo.upsert(_job(id=f"j{index}", last_seen_at=datetime(2026, 9, 1 + index, tzinfo=UTC)))
        assert [j.id for j in repo.list(limit=2, offset=0)] == ["j4", "j3"]
        assert [j.id for j in repo.list(limit=2, offset=2)] == ["j2", "j1"]

    def test_data_survives_reopen(self, tmp_path: Path) -> None:
        """持久化必须跨连接存续（不是内存态）。"""
        path = tmp_path / "persist.db"
        first = Database(path)
        first.initialize()
        first.jobs().upsert(_job())
        assert first.jobs().count() == 1

        reopened = Database(path)
        reopened.initialize()
        assert reopened.jobs().count() == 1
        assert reopened.jobs().get("j1") is not None


class TestJobSearch:
    """按关键词搜索岗位 —— 助手的 search_jobs 工具依赖它。"""

    def _seed(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="j1", title="AI产品经理", company_id="c1"))
        repo.upsert(_job(id="j2", title="大模型产品经理（2027校招）", company_id="c1"))
        repo.upsert(_job(id="j3", title="行政专员", company_id="c2"))
        repo.upsert(_job(id="j4", title="数据产品经理", company_id="c3"))

    def test_matches_normalized_title(self, db: Database) -> None:
        """搜「AI产品经理」应命中归一化后相同的项（含括号补充说明的那条）。"""
        self._seed(db)
        hits = db.jobs().search(keyword="产品经理")
        assert {j.id for j in hits} == {"j1", "j2", "j4"}

    def test_is_case_insensitive_and_fullwidth_tolerant(self, db: Database) -> None:
        self._seed(db)
        assert {j.id for j in db.jobs().search(keyword="ai产品")} == {"j1"}

    def test_no_match_returns_empty(self, db: Database) -> None:
        self._seed(db)
        assert db.jobs().search(keyword="不存在的岗位") == []

    def test_empty_keyword_returns_recent(self, db: Database) -> None:
        """空关键词 = 列出最近岗位（助手「看看有什么」的用法）。"""
        self._seed(db)
        hits = db.jobs().search(keyword="", limit=2)
        assert len(hits) == 2

    def test_respects_limit(self, db: Database) -> None:
        self._seed(db)
        assert len(db.jobs().search(keyword="产品经理", limit=2)) == 2

    def test_orders_by_last_seen_desc(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(
            _job(id="old", title="产品经理A", last_seen_at=datetime(2026, 9, 1, tzinfo=UTC))
        )
        repo.upsert(
            _job(id="new", title="产品经理B", last_seen_at=datetime(2026, 10, 1, tzinfo=UTC))
        )
        assert [j.id for j in repo.search(keyword="产品经理")] == ["new", "old"]
