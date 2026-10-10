"""抓取用例 —— 编排「跑一个适配器 → 把结果落库」。

只依赖 application 层的端口（`Crawler` / `JobRepository`），不接触具体
数据库或 HTTP 客户端 —— 因此可用假抓取器 + 真实 SQLite 完全离线测试。

设计选择：**抓取失败不抛出，而是放进 `CrawlResult.error`**。日更要跑几十上百
个公司，单个失败不该中断整轮；但也不能静默 —— 错误必须出现在结果里。
同理，**落库阶段的失败也收进 `error`**：只兜 `fetch()` 的话，一次 DB 写失败
（锁超时 / 磁盘满 / 库被占用）会穿出 `crawl_all`，后续站点一条都不跑。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hunter1.application.ports import Crawler, JobRepository
from hunter1.domain.crawl import RawJob, job_identity
from hunter1.domain.models import CaptureStatus, Job


@dataclass
class CrawlResult:
    """一次公司抓取的汇总结果。"""

    company: str
    site_key: str = ""
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
    result = CrawlResult(company=crawler.company, site_key=crawler.key)
    timestamp = now or datetime.now(UTC)

    try:
        raw_jobs = crawler.fetch()
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    result.fetched = len(raw_jobs)
    try:
        # 先**一次**问清楚「这一页里哪些岗位已经在库」，再逐条合并 —— 原先每条都
        # `jobs.get()`，一页 50 条就是 50 次往返（N+1）。合并本身需要旧记录的内容，
        # 所以省不掉读，只能把它从 N 次压成 1 次。
        job_ids = [
            job_identity(detail_url=raw.detail_url, company=raw.company, title=raw.title)
            for raw in raw_jobs
        ]
        existing_by_id = jobs.get_many(job_ids)
        for raw, job_id in zip(raw_jobs, job_ids, strict=True):
            existing = existing_by_id.get(job_id)
            if existing is None:
                fresh = _new_job(job_id, raw, timestamp)
                jobs.upsert_facts(fresh)
                # 写回映射：同一页里重复出现的同一个岗位，第二条要按「更新」处理
                # （旧实现逐条 `get` 时第二次能读到刚写的行，计数与合并行为都得跟上）
                existing_by_id[job_id] = fresh
                result.created += 1
            else:
                merged = _merge(existing, raw, timestamp)
                jobs.upsert_facts(merged)
                existing_by_id[job_id] = merged
                result.updated += 1
    except Exception as exc:
        # 落库失败同样收进结果，**不穿出去**：承诺是「单站失败不中断整轮」，
        # 一次 DB 写失败（锁超时 / 磁盘满 / 库被占用）不该让后续站点一条都不跑。
        # created/updated 保留已完成的计数 —— 那几条确实写进去了，不是零。
        result.error = f"{type(exc).__name__}: {exc}"

    return result


def _new_job(job_id: str, raw: RawJob, timestamp: datetime) -> Job:
    return Job(
        id=job_id,
        company_id=_company_id(raw),
        title=raw.title,
        detail_url=raw.detail_url,
        source=raw.source or raw.company,
        company_name=raw.company,
        city=raw.city,
        jd_raw=raw.jd_raw,
        capture_status=CaptureStatus.PENDING,
        first_seen_at=timestamp,
        last_seen_at=timestamp,
    )


def _merge(existing: Job, raw: RawJob, timestamp: datetime) -> Job:
    """把新抓到的事实合并进已有记录，**不覆盖已有成果**。

    刻意不走 `model_copy(update=...)`：pydantic v2 的 `model_copy` 不重跑
    校验器，「last_seen 被改到 first_seen 之前」这种非法状态会被静默造出来
    并直接写库（读回来才炸）。与 applications 切片的 `change_stage` 同一约定：
    合并走一次 `model_validate`，让领域不变量兜底。
    """
    payload = existing.model_dump(exclude_computed_fields=True)
    # 公司名是「事实」不是「成果」：站点填错/改名后重抓要能纠正。**id 必须跟着名字走**
    # —— 公司身份由公司名推导（见 `_company_id`），只改名字不改 id 会让同一家公司在库里
    # 留下两个 id：历史岗位挂旧哈希、之后新抓的岗位挂新哈希，按 `company_id` 分组/关联
    # 就此分裂。`_company_id` 只读 `company` 一个字段，所以这里不必伪造整个 RawJob。
    company_name = raw.company or existing.company_name
    payload.update(
        {
            "title": raw.title,
            "detail_url": raw.detail_url,
            "city": raw.city or existing.city,
            "company_name": company_name,
            "company_id": _company_id(raw.model_copy(update={"company": company_name})),
            # 已有 JD 正文不被空值抹掉
            "jd_raw": raw.jd_raw or existing.jd_raw,
            # last_seen 只前进不倒退：时间源异常（时钟回拨/旧数据）时保持原值，
            # 也保证 first_seen <= last_seen 的不变量在正常路径上不被破坏
            "last_seen_at": _later(existing.last_seen_at, timestamp),
            "capture_status": existing.capture_status,
            # match_score 保留在合并结果里（供调用方读），但 `upsert_facts` 不把它写库
            # —— 分数归评分切片所有，抓取路径不得覆盖（见 platform/db/repository.py）。
            "match_score": existing.match_score,
        }
    )
    return Job.model_validate(payload)


def _later(moment: datetime | None, other: datetime | None) -> datetime | None:
    """两个时刻中较晚的一个（None 视为「没有」）。"""
    if moment is None:
        return other
    if other is None:
        return moment
    return max(moment, other)


def _company_id(raw: RawJob) -> str:
    """公司 id：公司名的稳定哈希（sha256 前 128 位）。

    公司身份一律由公司名推导 —— `RawJob` 里没有 source_ref 字段，抓取层
    不产它。basis 格式（`ct:<公司名>:__company__`）是身份契约的一部分：
    改动它会让**已经写进 `jobs.company_id` 的取值**与后续抓取对不上
    （同一家公司在库里出现两个 id）。有测试锁定，见 test_service。

    诚实的边界：`companies` 表目前**没有任何写入点**（`Company` 模型是死代码），
    所以「改动会让全库公司关联断裂」这句话把影响说大了 —— 眼下的实际后果只是
    `jobs.company_id` 的分组错乱，再往后接公司维度的功能时才会真的疼。
    """
    return job_identity(detail_url="", company=raw.company, title="__company__")[:32]


def crawl_all(
    crawlers: Iterable[Crawler],
    *,
    jobs: JobRepository,
    now: datetime | None = None,
    on_result: Callable[[CrawlResult], None] | None = None,
) -> BatchCrawlResult:
    """跑一批适配器，逐个 upsert。

    单站失败**不中断**整轮（`crawl_company` 已把异常收进 `CrawlResult.error`），
    失败站点会出现在 `BatchCrawlResult.failures` 里 —— 不静默。

    整批共用同一个 `timestamp`：这样同一轮抓到的新岗位，`first_seen_at` 完全
    一致，事后能按「批次」还原「这一轮发生了什么」，而不是每个站点各自为政。

    `on_result` 每完成一个站点回调一次（成功失败都回调）——界面据此显示
    抓取进度，不必等整批结束。
    """
    timestamp = now or datetime.now(UTC)
    batch = BatchCrawlResult()
    for crawler in crawlers:
        result = crawl_company(crawler, jobs=jobs, now=timestamp)
        batch.results.append(result)
        if on_result is not None:
            on_result(result)
    return batch


__all__ = ["BatchCrawlResult", "CrawlResult", "crawl_all", "crawl_company"]
