"""抓取用例 —— 编排「跑一个适配器 → 把结果落库」。

只依赖 application 层的端口（`Crawler` / `JobRepository`），不接触具体
数据库或 HTTP 客户端 —— 因此可用假抓取器 + 真实 SQLite 完全离线测试。

设计选择：**抓取失败不抛出，而是放进 `CrawlResult.error`**。日更要跑几十上百
个公司，单个失败不该中断整轮；但也不能静默 —— 错误必须出现在结果里。
"""

from __future__ import annotations

from dataclasses import dataclass
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


__all__ = ["CrawlResult", "crawl_company"]
