"""jobs 切片用例测试 —— 真 SQLite，全程离线。

这是该切片的**独立验证入口**（`pytest tests/slices/jobs`）：
不依赖 web 层、不依赖网络，只测用例语义。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter1.domain.models import CaptureStatus, Job
from hunter1.platform.db import Database
from hunter1.slices.jobs.service import MAX_PAGE, find_job, list_jobs
from hunter1.slices.jobs.store import JobStore

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

# 三个岗位：前两个共享 8 位前缀（歧义用例），第三个前缀唯一
FULL_A = "a" * 32
FULL_B = "a" * 8 + "b" * 24
FULL_C = "c" * 32


def _seed(db: Database) -> None:
    repo = db.jobs()
    repo.upsert(
        Job(
            id=FULL_A,
            company_id="c1",
            title="AI产品经理",
            detail_url="https://x/1",
            source="实习僧",
            company_name="字节跳动",
            city="北京",
            match_score=88,
            capture_status=CaptureStatus.COMPLETE,
            jd_raw="负责大模型产品规划。",
            last_seen_at=datetime(2026, 10, 5, tzinfo=UTC),
        )
    )
    repo.upsert(
        Job(
            id=FULL_B,
            company_id="c2",
            title="大模型产品经理（2027校招）",
            detail_url="https://x/2",
            source="实习僧",
            company_name="某创业公司",
            last_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
        )
    )
    repo.upsert(
        Job(
            id=FULL_C,
            company_id="c3",
            title="行政专员",
            detail_url="https://x/3",
            source="实习僧",
            company_name="某国企",
            last_seen_at=datetime(2026, 10, 3, tzinfo=UTC),
        )
    )


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    db = Database(tmp_path / "jobs.db")
    db.initialize()
    _seed(db)
    return JobStore(db)


class TestListJobs:
    def test_lists_recent_first(self, store: JobStore) -> None:
        page = list_jobs(store)
        assert [job.id for job in page.items] == [FULL_A, FULL_B, FULL_C]
        assert page.total == 3
        assert page.page == 1
        assert page.has_next is False

    def test_keyword_uses_normalized_match(self, store: JobStore) -> None:
        """「产品经理」应命中含「（2027校招）」的条目（归一化匹配）。"""
        page = list_jobs(store, keyword="产品经理")
        assert {job.id for job in page.items} == {FULL_A, FULL_B}
        assert page.total == 2

    def test_pagination_slices_and_flags_next(self, store: JobStore) -> None:
        page = list_jobs(store, page=1, page_size=2)
        assert [job.id for job in page.items] == [FULL_A, FULL_B]
        assert page.has_next is True

        last = list_jobs(store, page=2, page_size=2)
        assert [job.id for job in last.items] == [FULL_C]
        assert last.has_next is False

    def test_page_is_clamped_to_max(self, store: JobStore) -> None:
        """页码上界：page=999999 必须被钳到 MAX_PAGE（防天量 OFFSET）。"""
        page = list_jobs(store, page=999_999)
        assert page.page == MAX_PAGE
        assert page.items == []

    def test_page_size_is_capped(self, store: JobStore) -> None:
        page = list_jobs(store, page_size=9999)
        assert page.page_size == 100

    def test_blank_keyword_lists_all(self, store: JobStore) -> None:
        assert list_jobs(store, keyword="   ").total == 3


class TestFindJob:
    def test_full_id(self, store: JobStore) -> None:
        job, ambiguous = find_job(store, FULL_A)
        assert job is not None and job.id == FULL_A
        assert ambiguous == 0

    def test_unique_prefix(self, store: JobStore) -> None:
        job, ambiguous = find_job(store, "a" * 8 + "b")
        assert job is not None and job.id == FULL_B
        assert ambiguous == 0

    def test_ambiguous_prefix_reports_count(self, store: JobStore) -> None:
        job, ambiguous = find_job(store, "a" * 8)
        assert job is None
        assert ambiguous == 2

    def test_missing(self, store: JobStore) -> None:
        job, ambiguous = find_job(store, "zzzz")
        assert job is None
        assert ambiguous == 0
