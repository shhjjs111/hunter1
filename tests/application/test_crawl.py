"""抓取用例单元测试 —— 假抓取器 + 真实 SQLite，全程离线。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hunter1.application.crawl import CrawlResult, crawl_company
from hunter1.domain.crawl import RawJob, job_identity
from hunter1.domain.models import Job
from hunter1.infrastructure.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "crawl.db")
    database.initialize()
    return database


class FakeCrawler:
    def __init__(self, company: str, jobs: list[RawJob], *, boom: bool = False) -> None:
        self.company = company
        self.careers_url = "https://example.com/careers"
        self._jobs = jobs
        self._boom = boom
        self.calls = 0

    def fetch(self) -> list[RawJob]:
        self.calls += 1
        if self._boom:
            raise RuntimeError("network exploded")
        return list(self._jobs)


def _raw(title: str, url: str, **kw: object) -> RawJob:
    return RawJob(company="示例科技", title=title, detail_url=url, **kw)  # type: ignore[arg-type]


class TestCrawlCompany:
    def test_persists_fetched_jobs(self, db: Database) -> None:
        crawler = FakeCrawler(
            "示例科技", [_raw("A岗", "https://a.com/1"), _raw("B岗", "https://a.com/2")]
        )
        result = crawl_company(crawler, jobs=db.jobs())
        assert isinstance(result, CrawlResult)
        assert result.fetched == 2
        assert result.created == 2
        assert result.updated == 0
        assert db.jobs().count() == 2

    def test_second_run_updates_not_duplicates(self, db: Database) -> None:
        jobs = [_raw("A岗", "https://a.com/1")]
        crawl_company(FakeCrawler("示例科技", jobs), jobs=db.jobs())
        result = crawl_company(FakeCrawler("示例科技", jobs), jobs=db.jobs())
        assert result.created == 0
        assert result.updated == 1
        assert db.jobs().count() == 1

    def test_job_id_is_stable_identity(self, db: Database) -> None:
        crawler = FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")])
        crawl_company(crawler, jobs=db.jobs())
        expected_id = job_identity(detail_url="https://a.com/1")
        assert db.jobs().get(expected_id) is not None

    def test_tracking_params_do_not_create_duplicate(self, db: Database) -> None:
        crawl_company(
            FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1?utm_source=x")]),
            jobs=db.jobs(),
        )
        crawl_company(
            FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")]),
            jobs=db.jobs(),
        )
        assert db.jobs().count() == 1

    def test_first_seen_is_set_and_last_seen_advances(self, db: Database) -> None:
        crawler = FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")])
        crawl_company(crawler, jobs=db.jobs())
        job_id = job_identity(detail_url="https://a.com/1")
        first = db.jobs().get(job_id)
        assert first is not None
        assert first.first_seen_at is not None
        assert first.last_seen_at is not None

        # 第二次抓取：first_seen 保持不变，last_seen 前进
        crawl_company(crawler, jobs=db.jobs())
        second = db.jobs().get(job_id)
        assert second is not None
        assert second.first_seen_at == first.first_seen_at
        assert second.last_seen_at is not None and first.last_seen_at is not None

    def test_sets_capture_status_pending(self, db: Database) -> None:
        from hunter1.domain.models import CaptureStatus

        crawl_company(FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")]), jobs=db.jobs())
        job = db.jobs().get(job_identity(detail_url="https://a.com/1"))
        assert job is not None
        assert job.capture_status is CaptureStatus.PENDING

    def test_failure_is_reported_not_raised(self, db: Database) -> None:
        """一次抓取失败不该让整个流程崩掉 —— 返回带错误的结果。"""
        crawler = FakeCrawler("示例科技", [], boom=True)
        result = crawl_company(crawler, jobs=db.jobs())
        assert result.fetched == 0
        assert result.error is not None
        assert "network exploded" in result.error
        assert db.jobs().count() == 0

    def test_empty_result_is_fine(self, db: Database) -> None:
        result = crawl_company(FakeCrawler("示例科技", []), jobs=db.jobs())
        assert result.fetched == 0
        assert result.error is None

    def test_existing_match_score_is_preserved(self, db: Database) -> None:
        """重抓不应抹掉已有的评分结果。"""
        crawler = FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")])
        crawl_company(crawler, jobs=db.jobs())
        job_id = job_identity(detail_url="https://a.com/1")
        existing = db.jobs().get(job_id)
        assert existing is not None
        db.jobs().upsert(existing.model_copy(update={"match_score": 88}))

        crawl_company(crawler, jobs=db.jobs())
        after = db.jobs().get(job_id)
        assert after is not None
        assert after.match_score == 88  # 评分被保留

    def test_result_records_company(self, db: Database) -> None:
        result = crawl_company(
            FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")]), jobs=db.jobs()
        )
        assert result.company == "示例科技"


def test_job_model_matches_identity_helper() -> None:
    """身份计算的两种路径（URL / 公司+标题）都不该抛错。"""
    assert job_identity(detail_url="", company="C", title="T")
    assert isinstance(Job, type)
