"""抓取用例 —— 编排「跑一个适配器 → 把结果落库」。

只依赖 application 层的端口（`Crawler` / `JobRepository`），不接触具体
数据库或 HTTP 客户端 —— 因此可用假抓取器 + 真实 SQLite 完全离线测试。

设计选择：**抓取失败不抛出，而是放进 `CrawlResult.error`**。日更要跑几十上百
个公司，单个失败不该中断整轮；但也不能静默 —— 错误必须出现在结果里。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hunter1.application.ports import Crawler, JobRepository
from hunter1.domain.crawl import RawJob, job_identity
from hunter1.domain.models import CaptureStatus, Job


@dataclass
class CrawlResult:
    """一次公司抓取的汇总结果。"""

    company: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class BatchCrawlResult:
    """一轮多站点抓取的汇总。"""

    results: list[CrawlResult] = field(default_factory=list)

    @property
    def fetched(self) -> int:
        return sum(r.fetched for r in self.results)

    @property
    def created(self) -> int:
        return sum(r.created for r in self.results)

    @property
    def updated(self) -> int:
        return sum(r.updated for r in self.results)

    @property
    def failures(self) -> list[CrawlResult]:
        return [r for r in self.results if not r.ok]

    @property
    def ok(self) -> bool:
        return not self.failures


def crawl_company(
    crawler: Crawler,
    *,
    jobs: JobRepository,
    now: datetime | None = None,
) -> CrawlResult:
    """跑一个适配器并把结果 upsert 进岗位库。

    保持既有字段的不变量：
    - `first_seen_at` 只在首次入库时写，之后不动；
    - `last_seen_at` 每次成功抓到都前进；
    - 已有的 `match_score` / `jd_raw` 不被空值覆盖。
    """
    result = CrawlResult(company=crawler.company)
    timestamp = now or datetime.now(UTC)

    try:
        raw_jobs = crawler.fetch()
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    result.fetched = len(raw_jobs)
    for raw in raw_jobs:
        job_id = job_identity(detail_url=raw.detail_url, company=raw.company, title=raw.title)
        existing = jobs.get(job_id)
        if existing is None:
            jobs.upsert(_new_job(job_id, raw, timestamp))
            result.created += 1
        else:
            jobs.upsert(_merge(existing, raw, timestamp))
            result.updated += 1

    return result


def _new_job(job_id: str, raw: RawJob, timestamp: datetime) -> Job:
    return Job(
        id=job_id,
        company_id=_company_id(raw),
        title=raw.title,
        detail_url=raw.detail_url,
        source=raw.source or raw.company,
        city=raw.city,
        jd_raw=raw.jd_raw,
        capture_status=CaptureStatus.PENDING,
        first_seen_at=timestamp,
        last_seen_at=timestamp,
    )


def _merge(existing: Job, raw: RawJob, timestamp: datetime) -> Job:
    """把新抓到的事实合并进已有记录，**不覆盖已有成果**。"""
    return existing.model_copy(
        update={
            "title": raw.title,
            "detail_url": raw.detail_url,
            "city": raw.city or existing.city,
            # 已有 JD 正文不被空值抹掉
            "jd_raw": raw.jd_raw or existing.jd_raw,
            "last_seen_at": timestamp,
            "capture_status": existing.capture_status,
            "match_score": existing.match_score,
        }
    )


def _company_id(raw: RawJob) -> str:
    """公司 id：优先用调用方给的 source_ref，否则用公司名的稳定哈希。"""
    return job_identity(detail_url="", company=raw.company, title="__company__")[:32]


def crawl_all(
    crawlers: Iterable[Crawler],
    *,
    jobs: JobRepository,
    now: datetime | None = None,
) -> BatchCrawlResult:
    """跑一批适配器，逐个 upsert。

    单站失败**不中断**整轮（`crawl_company` 已把异常收进 `CrawlResult.error`），
    失败站点会出现在 `BatchCrawlResult.failures` 里 —— 不静默。

    整批共用同一个 `timestamp`：这样同一轮抓到的新岗位，`first_seen_at` 完全
    一致，事后能按「批次」还原「这一轮发生了什么」，而不是每个站点各自为政。
    """
    timestamp = now or datetime.now(UTC)
    batch = BatchCrawlResult()
    for crawler in crawlers:
        batch.results.append(crawl_company(crawler, jobs=jobs, now=timestamp))
    return batch


__all__ = ["BatchCrawlResult", "CrawlResult", "crawl_all", "crawl_company"]
