"""抢取用例测试 —— 假抓取器 + 真实 SQLite，全程离线。

（从旧 `tests/application/test_crawl.py` 迁移：断言语义逐条保留，
只改 import 路径。）
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter1.domain.crawl import RawJob, job_identity
from hunter1.domain.models import Job
from hunter1.platform.db import Database
from hunter1.slices.crawl import ListPageSpec, StaticHtmlCrawler
from hunter1.slices.crawl.service import (
    BatchCrawlResult,
    CrawlResult,
    crawl_all,
    crawl_company,
)


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "crawl.db")
    database.initialize()
    return database


class FakeCrawler:
    def __init__(
        self, company: str, jobs: list[RawJob], *, boom: bool = False, key: str = ""
    ) -> None:
        self.key = key or company
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


def _raw(title: str, url: str, *, company: str = "示例科技", **kw: object) -> RawJob:
    return RawJob(company=company, title=title, detail_url=url, **kw)  # type: ignore[arg-type]


class FlakyJobs:
    """包装真实仓储，让接下来 `failures` 次 `upsert_facts` 抛错（模拟 DB 写失败）。

    真实触发形态：锁超时 / 磁盘满 / 库被另一个进程占着。除 `upsert_facts` 外
    全部转发给真件，所以计数与查询仍是真实行为。
    """

    def __init__(self, inner: object, *, failures: int = 1) -> None:
        self._inner = inner
        self._failures = failures

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def upsert_facts(self, job: Job) -> None:
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("database is locked")
        self._inner.upsert_facts(job)  # type: ignore[attr-defined]


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

    def test_records_company_name_for_display(self, db: Database) -> None:
        """落库时要带上人读的公司名 —— company_id 是哈希，界面显示不了。"""
        crawler = FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")])
        crawl_company(crawler, jobs=db.jobs())
        job = db.jobs().get(job_identity(detail_url="https://a.com/1"))
        assert job is not None and job.company_name == "示例科技"

    def test_company_name_updates_when_corrected(self, db: Database) -> None:
        """公司名写错后重抓应能纠正 —— 它不像 match_score 是「已有成果」。"""
        crawl_company(FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")]), jobs=db.jobs())
        crawl_company(
            FakeCrawler(
                "示例科技",
                [_raw("A岗", "https://a.com/1", company="示例科技有限公司")],
            ),
            jobs=db.jobs(),
        )
        job = db.jobs().get(job_identity(detail_url="https://a.com/1"))
        assert job is not None and job.company_name == "示例科技有限公司"

    def test_merge_keeps_first_seen_not_later_than_last_seen(self, db: Database) -> None:
        """合并不得绕过领域不变量：last_seen 改到 first_seen 之前必须被拦住。

        `model_copy(update=...)` 不重跑 pydantic 校验器，会把
        last_seen(新) < first_seen(旧) 的非法对象直接写库 —— 读回时才炸。
        这里模拟「库里带未来时间戳的旧数据」（旧库迁移/手工改动/时区异常），
        再抓一次：修复前 get() 读回即抛 ValidationError。
        """
        job_id = job_identity(detail_url="https://a.com/1")
        future = datetime(2027, 1, 1, tzinfo=UTC)
        db.jobs().upsert(
            Job(
                id=job_id,
                company_id="c1",
                title="A岗",
                detail_url="https://a.com/1",
                source="示例科技",
                first_seen_at=future,
                last_seen_at=future,
            )
        )
        result = crawl_company(
            FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")]),
            jobs=db.jobs(),
            now=datetime(2026, 10, 5, tzinfo=UTC),
        )
        assert result.updated == 1
        after = db.jobs().get(job_id)
        assert after is not None
        assert after.first_seen_at is not None and after.last_seen_at is not None
        assert after.first_seen_at <= after.last_seen_at
        # last_seen 只前进不倒退：时钟倒流时保持原值
        assert after.last_seen_at == future

    def test_company_id_is_stable_across_runs(self, db: Database) -> None:
        """公司 id 是身份契约：basis 格式一变，全库公司关联断裂且无法自动修复。

        锁定值 = sha256("ct:示例科技:__company__")[:32]（见 service._company_id）。
        如果有人改动哈希 basis 格式，这个测试会红 —— 那正是「静默失败」变显式的时刻。
        """
        crawl_company(FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")]), jobs=db.jobs())
        job = db.jobs().get(job_identity(detail_url="https://a.com/1"))
        assert job is not None
        assert job.company_id == "962b18655b5db8eafd8fece6edfd4ef9"

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

    def test_persistence_failure_is_reported_not_raised(self, db: Database) -> None:
        """**落库**阶段失败同样收进 error —— 原先只兜 fetch()，异常会穿出去。"""
        crawler = FakeCrawler("示例科技", [_raw("A岗", "https://a.com/1")])
        result = crawl_company(crawler, jobs=FlakyJobs(db.jobs()))  # type: ignore[arg-type]
        assert result.fetched == 1  # 确实抓到了
        assert result.created == 0  # 但没落库
        assert result.error is not None
        assert "locked" in result.error
        assert result.ok is False
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


def test_job_identity_paths_follow_the_documented_basis() -> None:
    """身份契约：两种 basis 的**格式**是对外可见的，改它会让已入库的 id 失配。

    抓取切片把 basis 格式（`ct:<公司>:__company__`）当「身份契约的一部分」写进了
    `_company_id` 的文档 —— 那就该有断言钉住它。

    原先这条只断言 `isinstance(Job, type)`：**断言一个类是类**。把 Job 换成
    dataclass / 协议 / 函数都绿，`job_identity` 的 basis 格式改掉也绿。
    """
    url_id = job_identity(detail_url="https://a.com/1")
    assert url_id == hashlib.sha256(b"url:https://a.com/1").hexdigest()

    # 公司 + 标题这条路径：标题先经 `normalize_job_title`（NFKC + 去括号 + 小写）
    company_id = job_identity(detail_url="", company="示例科技", title="A岗（急招）")
    assert company_id == hashlib.sha256("ct:示例科技:a岗".encode()).hexdigest()

    # 两条路径必须给出**不同**的身份 —— 否则数据不全时会与某个 URL 身份撞号
    assert url_id != company_id

    # 两者都没有 → 明确拒绝，而不是为「没有身份的东西」生成一个 id
    with pytest.raises(ValueError):
        job_identity(detail_url="", company="只有公司没有标题")


class TestCrawlAll:
    """批量抓取：日更要跑几十个站点，单个失败不该中断整轮。"""

    def test_aggregates_counts_across_crawlers(self, db: Database) -> None:
        crawlers = [
            FakeCrawler("甲", [_raw("A岗", "https://a.com/1"), _raw("B岗", "https://a.com/2")]),
            FakeCrawler("乙", [_raw("C岗", "https://b.com/1")]),
        ]
        batch = crawl_all(crawlers, jobs=db.jobs())
        assert batch.fetched == 3
        assert batch.created == 3
        assert batch.updated == 0
        assert db.jobs().count() == 3
        assert [r.company for r in batch.results] == ["甲", "乙"]

    def test_one_failure_does_not_stop_others(self, db: Database) -> None:
        crawlers = [
            FakeCrawler("甲", [], boom=True),
            FakeCrawler("乙", [_raw("C岗", "https://b.com/1")]),
        ]
        batch = crawl_all(crawlers, jobs=db.jobs())
        assert batch.fetched == 1  # 乙照常抓到
        assert db.jobs().count() == 1
        assert [r.company for r in batch.failures] == ["甲"]
        assert len(batch.results) == 2
        assert batch.ok is False

    def test_persistence_failure_does_not_stop_others(self, db: Database) -> None:
        """DB 写失败也只算「这个站点失败」—— 后续站点必须照跑。

        原先只兜 `crawler.fetch()`，一次落库异常会穿出 `crawl_all`：
        后面的站点一条都不跑，而结果里连失败记录都没有。
        """
        crawlers = [
            FakeCrawler("甲", [_raw("A岗", "https://a.com/1")]),
            FakeCrawler("乙", [_raw("C岗", "https://b.com/1")]),
        ]
        batch = crawl_all(crawlers, jobs=FlakyJobs(db.jobs()))  # type: ignore[arg-type]

        assert [r.error is not None for r in batch.results] == [True, False]
        assert batch.results[0].fetched == 1  # 甲抓到了、但没写进去
        assert [r.company for r in batch.failures] == ["甲"]
        assert db.jobs().count() == 1  # 乙的那条照常落库
        assert batch.ok is False

    def test_all_ok_flag(self, db: Database) -> None:
        batch = crawl_all([FakeCrawler("甲", [_raw("A岗", "https://a.com/1")])], jobs=db.jobs())
        assert batch.ok is True
        assert batch.failures == []

    def test_empty_batch_is_ok(self, db: Database) -> None:
        batch = crawl_all([], jobs=db.jobs())
        assert isinstance(batch, BatchCrawlResult)
        assert batch.results == []
        assert batch.fetched == 0
        assert batch.ok is True

    def test_second_run_updates_instead_of_duplicating(self, db: Database) -> None:
        crawlers = [FakeCrawler("甲", [_raw("A岗", "https://a.com/1")])]
        crawl_all(crawlers, jobs=db.jobs())
        batch = crawl_all(crawlers, jobs=db.jobs())
        assert batch.created == 0
        assert batch.updated == 1
        assert db.jobs().count() == 1

    def test_batch_shares_one_timestamp(self, db: Database) -> None:
        """同一批次里两个站点的 first_seen 应完全相同 —— 用同一个批次时刻。"""
        crawlers = [
            FakeCrawler("甲", [_raw("A岗", "https://a.com/1")]),
            FakeCrawler("乙", [_raw("C岗", "https://b.com/1")]),
        ]
        crawl_all(crawlers, jobs=db.jobs())
        first = db.jobs().get(job_identity(detail_url="https://a.com/1"))
        second = db.jobs().get(job_identity(detail_url="https://b.com/1"))
        assert first is not None and second is not None
        assert first.first_seen_at == second.first_seen_at

    def test_on_result_reports_each_site_as_it_finishes(self, db: Database) -> None:
        """进度回调：每站跑完就报一次，供界面显示「正在抓哪个」。"""
        crawlers = [
            FakeCrawler("甲", [_raw("A岗", "https://a.com/1")]),
            FakeCrawler("乙", [], boom=True),
            FakeCrawler("丙", [_raw("C岗", "https://c.com/1")]),
        ]
        seen: list[tuple[str, int, str | None]] = []
        crawl_all(
            crawlers,
            jobs=db.jobs(),
            on_result=lambda r: seen.append((r.company, r.fetched, r.error)),
        )
        assert [company for company, _, _ in seen] == ["甲", "乙", "丙"]
        assert seen[0][1] == 1
        assert seen[1][2] is not None  # 失败也照样回调，不被吞掉
        assert len(seen) == 3


class _FixedFetcher:
    def __init__(self, html: str) -> None:
        self.html = html

    def get_text(self, url: str, **_kw: object) -> str:
        return self.html


CHALLENGE_PAGE = (
    '<html><head><title id="pageTitle">安全验证 - BOSS直聘</title></head>'
    '<body><div class="verify-row-code">请完成安全验证</div></body></html>'
)


def test_blocked_page_is_reported_as_error_not_empty_result(db: Database) -> None:
    """被风控拦下时必须报错 —— 「被拦截」和「今天没岗位」不能长得一样。"""
    crawler = StaticHtmlCrawler(
        company="某站",
        careers_url="https://site.com/jobs",
        spec=ListPageSpec(
            url_template="https://site.com/jobs",
            item_selector="li.job",
            title_selector="a.t",
        ),
        fetcher=_FixedFetcher(CHALLENGE_PAGE),
    )
    result = crawl_company(crawler, jobs=db.jobs())
    assert result.error is not None
    assert "blocked" in result.error
    assert result.fetched == 0
