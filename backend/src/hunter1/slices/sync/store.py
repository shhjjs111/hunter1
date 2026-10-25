"""同步快照的导出与合并 —— 只经各切片的公开门面读写。

冲突规则（刻意简单、可解释）：

- 岗位事实：`last_seen_at` 较新的一方胜；相等时保留电脑端现值。
- 岗位评分：`scored_at` 较新的一方胜；没有评分的一方不覆盖已有评分。
- 投递：`updated_at` 较新的一方胜；按 `job_detail_url` 对齐，而不是 UUID。
- 画像：`updated_at` 较新的一方胜。

跨设备真正稳定的身份是详情页 URL。手机与电脑各自抓到的同一岗位会有不同的
UUID，因此所有合并都先按 URL 归一再判断「是不是同一条」。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from hunter1.domain.crawl import company_identity, job_identity, normalize_detail_url
from hunter1.domain.models import Application, CaptureStatus, Job
from hunter1.platform.db import Database
from hunter1.slices.applications import ApplicationStore
from hunter1.slices.jobs import JobStore
from hunter1.slices.scoring import ScoreStore
from hunter1.slices.sync.schemas import (
    SyncApplication,
    SyncCounts,
    SyncJob,
    SyncProfile,
    SyncSnapshot,
    SyncSnapshotImport,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: 旧数据没有画像时间戳时的兜底：任何带真实时间的同步都能覆盖它。
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _newer(candidate: datetime | None, current: datetime | None) -> bool:
    """`candidate` 是否严格晚于 `current`（缺失视为最旧）。"""
    left = _utc(candidate)
    right = _utc(current)
    if left is None:
        return False
    if right is None:
        return True
    return left > right


def _later(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _earlier(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _sync_job(job: Job) -> SyncJob:
    return SyncJob(
        id=job.id,
        title=job.title,
        company=job.company_name or job.source,
        city=job.city,
        source=job.source,
        detail_url=job.detail_url,
        jd_raw=job.jd_raw,
        capture_status=job.capture_status,
        match_score=job.match_score,
        score_summary=job.score_summary,
        score_advantages=job.score_advantages,
        score_gaps=job.score_gaps,
        score_model=job.score_model,
        score_prompt_version=job.score_prompt_version,
        scored_at=job.scored_at,
        first_seen_at=job.first_seen_at,
        last_seen_at=job.last_seen_at,
    )


def _sync_application(application: Application, job_url: str | None) -> SyncApplication:
    return SyncApplication(
        job_detail_url=job_url,
        title=application.title,
        company=application.company,
        stage=application.stage,
        note=application.note,
        applied_at=application.applied_at,
        updated_at=application.updated_at,
    )


class SyncStore:
    """跨设备同步的快照门面。"""

    def __init__(
        self,
        *,
        db: Database,
        jobs: JobStore,
        applications: ApplicationStore,
        scores: ScoreStore,
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._jobs = jobs
        self._applications = applications
        self._scores = scores
        self._clock = clock

    # ---- 导出 ----

    def export_snapshot(self) -> SyncSnapshot:
        """导出本机全部可同步数据。

        **一条坏数据不能让整份快照 500**：JD 可能因截断提示刚好超长、详情 URL
        可能为空、画像可能损坏。这里统一用宽松的导出模型；画像读失败按「未配置」
        处理（并留一行 stderr，避免静默吞掉数据损坏）。
        """
        with self._db.session() as session:
            jobs = self._jobs.all(session=session)
            applications = self._applications.all(session=session)
            try:
                profile = self._scores.load_profile(session=session)
            except ValueError as exc:
                print(
                    f"同步导出：本机候选人画像不可用，已按未配置处理：{exc}",
                    file=sys.stderr,
                )
                profile = None
            profile_at = self._scores.load_profile_updated_at(session=session) or EPOCH
        job_urls = {job.id: job.detail_url for job in jobs}
        return SyncSnapshot(
            exported_at=_utc(self._clock()) or datetime.now(UTC),
            jobs=[_sync_job(job) for job in jobs],
            applications=[
                _sync_application(application, job_urls.get(application.job_id))
                for application in applications
            ],
            profile=SyncProfile(
                keywords=profile.keywords,
                directions=profile.directions,
                summary=profile.summary,
                updated_at=profile_at,
            )
            if profile is not None
            else None,
        )

    # ---- 合并 ----

    def merge_snapshot(self, snapshot: SyncSnapshotImport) -> tuple[SyncCounts, SyncSnapshot]:
        """把另一台设备的快照合并进本机，并返回合并后的规范快照。

        整段写入包在**一个事务**里：岗位、评分、投递、画像分属不同切片，但必须
        一起成功或一起回滚。否则中途失败会让调用方看到 422/500，本机却已经改了
        一半（旧实现每一步各自 commit）。
        """
        now = _utc(self._clock()) or datetime.now(UTC)
        counts = SyncCounts()
        # 先校验画像，再动任何一行：坏画像属于可直接拒绝的输入问题。
        incoming_profile = snapshot.profile.to_candidate() if snapshot.profile else None

        with self._db.transaction() as session:
            by_url: dict[str, Job] = {}
            for job in self._jobs.all(session=session):
                url = normalize_detail_url(job.detail_url)
                if url:
                    by_url[url] = job

            incoming_jobs: dict[str, SyncJob] = {}
            for item in snapshot.jobs:
                url = normalize_detail_url(item.detail_url)
                if not url:
                    continue
                previous = incoming_jobs.get(url)
                if previous is None:
                    incoming_jobs[url] = item
                    continue
                # 同一 URL 的事实和评分有独立版本；整行选胜者会丢掉另一副本的新评分。
                facts = item if _newer(item.last_seen_at, previous.last_seen_at) else previous
                score = (
                    item
                    if item.match_score is not None
                    and (previous.match_score is None or _newer(item.scored_at, previous.scored_at))
                    else previous
                )
                incoming_jobs[url] = facts.model_copy(
                    update={
                        field: getattr(score, field)
                        for field in (
                            "match_score",
                            "score_summary",
                            "score_advantages",
                            "score_gaps",
                            "score_model",
                            "score_prompt_version",
                            "scored_at",
                        )
                    }
                )

            for url, item in incoming_jobs.items():
                existing = by_url.get(url)
                if existing is None:
                    created = self._create_job(item, url, now, session=session)
                    self._jobs.upsert_facts(created, session=session)
                    self._write_score(created, item, session=session)
                    by_url[url] = created
                    counts.jobs_created += 1
                    continue

                merged, facts_changed, score_changed = self._merge_job(existing, item, url)
                if facts_changed:
                    self._jobs.upsert_facts(merged, session=session)
                if score_changed:
                    self._write_score(merged, item, session=session)
                by_url[url] = merged
                if facts_changed or score_changed:
                    counts.jobs_updated += 1
                else:
                    counts.jobs_kept += 1

            existing_applications = {
                app.job_id: app for app in self._applications.all(session=session)
            }
            incoming_applications: dict[str, SyncApplication] = {}
            for item in snapshot.applications:
                url = normalize_detail_url(item.job_detail_url or "")
                if not url:
                    continue
                previous = incoming_applications.get(url)
                if previous is None or _newer(item.updated_at, previous.updated_at):
                    incoming_applications[url] = item

            for url, item in incoming_applications.items():
                job = by_url.get(url)
                if job is None:
                    # 投递记录指向的岗位不在这份快照里：不猜、不建半条记录。
                    counts.applications_skipped += 1
                    continue
                current = existing_applications.get(job.id)
                if current is None:
                    created = Application(
                        id=uuid4().hex,
                        job_id=job.id,
                        company=item.company,
                        title=item.title,
                        stage=item.stage,
                        applied_at=_utc(item.applied_at) or now,
                        updated_at=_utc(item.updated_at) or now,
                        note=item.note,
                    )
                    stored = self._applications.insert_for_job(created, session=session)
                    existing_applications[job.id] = stored
                    counts.applications_created += 1
                    continue
                if _newer(item.updated_at, current.updated_at):
                    updated = Application(
                        id=current.id,
                        job_id=job.id,
                        company=item.company,
                        title=item.title,
                        stage=item.stage,
                        applied_at=_utc(item.applied_at) or current.applied_at,
                        updated_at=_utc(item.updated_at) or current.updated_at,
                        note=item.note,
                    )
                    self._applications.update_existing(updated, session=session)
                    existing_applications[job.id] = updated
                    counts.applications_updated += 1
                else:
                    counts.applications_kept += 1

            if snapshot.profile is not None and incoming_profile is not None:
                current_at = self._scores.load_profile_updated_at(session=session)
                current_profile = self._scores.load_profile(session=session)
                if current_profile is None or _newer(snapshot.profile.updated_at, current_at):
                    self._scores.save_profile_at(
                        incoming_profile,
                        _utc(snapshot.profile.updated_at) or now,
                        session=session,
                    )
                    counts.profile_updated = True

        return counts, self.export_snapshot()

    def _create_job(
        self, item: SyncJob, url: str, now: datetime, *, session: Session | None = None
    ) -> Job:
        # 客户端可能只带 last_seen_at（手机 JobRecord 不存 first_seen）。
        # 此时 first_seen 必须退到 last_seen，而不是服务器当前时间 —— 后者可能
        # 晚于客户端的 last_seen，直接违反 first_seen <= last_seen 的不变量。
        last_seen = _utc(item.last_seen_at)
        first_seen = _utc(item.first_seen_at) or last_seen or now
        last_seen = last_seen or first_seen
        candidate_id = job_identity(detail_url=url, company=item.company, title=item.title)
        if self._jobs.get(candidate_id, session=session) is not None:
            # 极窄冲突：同一个 UUID 已属于另一条 URL。退回随机 id，副本仍按 URL 去重。
            candidate_id = uuid4().hex
        return Job(
            id=candidate_id,
            company_id=company_identity(item.company),
            title=item.title,
            detail_url=url,
            source=item.source or item.company,
            company_name=item.company,
            city=item.city,
            jd_raw=item.jd_raw,
            capture_status=item.capture_status,
            first_seen_at=first_seen,
            last_seen_at=last_seen,
        )

    def _merge_job(self, existing: Job, item: SyncJob, url: str) -> tuple[Job, bool, bool]:
        incoming_newer = _newer(item.last_seen_at, existing.last_seen_at)
        company = (
            item.company if incoming_newer or not existing.company_name else existing.company_name
        )
        title = item.title if incoming_newer else existing.title
        # 较旧的快照只能补空，不能覆盖较新一方的标题 / JD / 城市。
        jd_raw = (
            (item.jd_raw or existing.jd_raw) if incoming_newer else (existing.jd_raw or item.jd_raw)
        )
        city = (item.city or existing.city) if incoming_newer else (existing.city or item.city)
        source = (
            (item.source or existing.source) if incoming_newer else (existing.source or item.source)
        )
        fill_capture = item.capture_status is not CaptureStatus.UNKNOWN and (
            incoming_newer or existing.capture_status is CaptureStatus.UNKNOWN
        )
        capture_status = item.capture_status if fill_capture else existing.capture_status
        merged = Job(
            id=existing.id,
            company_id=company_identity(company),
            title=title,
            detail_url=url,
            source=source,
            company_name=company,
            city=city,
            jd_raw=jd_raw,
            capture_status=capture_status,
            first_seen_at=_earlier(existing.first_seen_at, _utc(item.first_seen_at)),
            last_seen_at=_later(existing.last_seen_at, _utc(item.last_seen_at)),
            match_score=existing.match_score,
            score_summary=existing.score_summary,
            score_advantages=existing.score_advantages,
            score_gaps=existing.score_gaps,
            score_model=existing.score_model,
            score_prompt_version=existing.score_prompt_version,
            scored_at=existing.scored_at,
        )
        score_changed = item.match_score is not None and (
            existing.match_score is None or _newer(item.scored_at, existing.scored_at)
        )
        if score_changed:
            merged = Job.model_validate(
                merged.model_dump(exclude_computed_fields=True)
                | {
                    "match_score": item.match_score,
                    "score_summary": item.score_summary,
                    "score_advantages": item.score_advantages,
                    "score_gaps": item.score_gaps,
                    "score_model": item.score_model,
                    "score_prompt_version": item.score_prompt_version,
                    "scored_at": _utc(item.scored_at),
                }
            )
        facts = (
            "title",
            "company_id",
            "company_name",
            "city",
            "source",
            "detail_url",
            "jd_raw",
            "capture_status",
            "first_seen_at",
            "last_seen_at",
        )
        facts_changed = any(getattr(existing, field) != getattr(merged, field) for field in facts)
        return merged, facts_changed, score_changed

    def _write_score(self, job: Job, item: SyncJob, *, session: Session | None = None) -> None:
        if item.match_score is None:
            return
        self._scores.save_score(
            job.id,
            item.match_score,
            model=item.score_model,
            prompt_version=item.score_prompt_version,
            scored_at=_utc(item.scored_at),
            summary=item.score_summary,
            advantages=item.score_advantages,
            gaps=item.score_gaps,
            session=session,
        )


__all__ = ["EPOCH", "SyncStore"]
