"""投递用例单元测试。

`new_application` / `change_stage` 是纯领域逻辑，无 IO（迁移自
`tests/application/test_applications.py`：断言语义逐条保留）。
`apply_to_job`（记录投递，迁移自 jobs 切片的同名用例）需要落库，用真 SQLite。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter1.domain.models import ApplicationStage, Job
from hunter1.platform.db import Database
from hunter1.slices.applications.service import apply_to_job, change_stage, new_application
from hunter1.slices.applications.store import ApplicationStore


def _job(**overrides: object) -> Job:
    base: dict[str, object] = {
        "id": "j1",
        "company_id": "c1",
        "title": "AI产品经理",
        "detail_url": "https://a.com/1",
        "source": "实习僧",
        "company_name": "字节跳动",
    }
    base.update(overrides)
    return Job(**base)  # type: ignore[arg-type]


NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class TestNewApplication:
    def test_snapshots_company_and_title(self) -> None:
        application = new_application(job=_job(), now=NOW)
        assert application.company == "字节跳动"
        assert application.title == "AI产品经理"
        assert application.job_id == "j1"

    def test_falls_back_to_source_when_company_name_missing(self) -> None:
        """早期抓到的岗位没有公司名 —— 退到 source，不留空。"""
        application = new_application(job=_job(company_name=None), now=NOW)
        assert application.company == "实习僧"

    def test_defaults_to_applied_stage(self) -> None:
        assert new_application(job=_job(), now=NOW).stage is ApplicationStage.APPLIED

    def test_applied_and_updated_are_the_same_instant(self) -> None:
        application = new_application(job=_job(), now=NOW)
        assert application.applied_at == NOW
        assert application.updated_at == NOW

    def test_accepts_explicit_id(self) -> None:
        assert new_application(job=_job(), now=NOW, application_id="a1").id == "a1"

    def test_generates_distinct_ids(self) -> None:
        first = new_application(job=_job(), now=NOW)
        second = new_application(job=_job(), now=NOW)
        assert first.id != second.id

    def test_note_is_optional(self) -> None:
        assert new_application(job=_job(), now=NOW).note is None
        assert new_application(job=_job(), now=NOW, note="内推").note == "内推"


class TestChangeStage:
    def test_advances_stage_and_time(self) -> None:
        application = new_application(job=_job(), now=NOW)
        later = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
        moved = change_stage(application, stage=ApplicationStage.INTERVIEW, now=later)
        assert moved.stage is ApplicationStage.INTERVIEW
        assert moved.updated_at == later
        assert moved.applied_at == NOW  # 投递时间不动

    def test_keeps_existing_note_when_not_given(self) -> None:
        application = new_application(job=_job(), now=NOW, note="内推")
        moved = change_stage(application, stage=ApplicationStage.OFFER, now=NOW)
        assert moved.note == "内推"

    def test_replaces_note_when_given(self) -> None:
        application = new_application(job=_job(), now=NOW, note="内推")
        moved = change_stage(application, stage=ApplicationStage.OFFER, now=NOW, note="口头 offer")
        assert moved.note == "口头 offer"

    def test_does_not_mutate_the_original(self) -> None:
        application = new_application(job=_job(), now=NOW)
        change_stage(application, stage=ApplicationStage.REJECTED, now=NOW)
        assert application.stage is ApplicationStage.APPLIED

    def test_time_travelling_backwards_is_rejected(self) -> None:
        """把更新时刻设到投递之前是不可能的 —— 领域模型直接拒绝。"""
        application = new_application(job=_job(), now=NOW)
        with pytest.raises(ValueError):
            change_stage(
                application,
                stage=ApplicationStage.INTERVIEW,
                now=datetime(2026, 9, 1, tzinfo=UTC),
            )


@pytest.fixture()
def store(tmp_path: Path) -> ApplicationStore:
    db = Database(tmp_path / "applications-service.db")
    db.initialize()
    return ApplicationStore(db)


class TestApplyToJob:
    """记录投递（迁移自 jobs 切片的 TestApply）。"""

    def test_snapshots_company_and_title(self, store: ApplicationStore) -> None:
        """投递要快照公司名与标题（岗位库清空后记录仍可读）。"""
        outcome = apply_to_job(store=store, job=_job(), now=NOW)
        assert outcome.application.job_id == "j1"
        assert outcome.application.company == "字节跳动"
        assert outcome.application.title == "AI产品经理"

    def test_is_persisted(self, store: ApplicationStore) -> None:
        outcome = apply_to_job(store=store, job=_job(), now=NOW)
        assert store.get(outcome.application.id) is not None

    def test_first_apply_is_reported_as_created(self, store: ApplicationStore) -> None:
        """第一次投递是新建（路由据此回 201）。"""
        assert apply_to_job(store=store, job=_job(), now=NOW).created is True

    def test_repeat_apply_is_idempotent(self, store: ApplicationStore) -> None:
        """重复投递同一岗位只产生一条记录 —— 重复点击不该堆出多条投递。"""
        first = apply_to_job(store=store, job=_job(), now=NOW)
        second = apply_to_job(store=store, job=_job(), now=NOW)
        assert second.application.id == first.application.id
        assert second.created is False
        assert len(store.by_job("j1")) == 1

    def test_concurrent_apply_keeps_a_single_record(self, store: ApplicationStore) -> None:
        """并发投递同一岗位仍只留一条 —— 唯一约束是最后一道闸。

        两个线程同时越过「先读」（都读到空）时，第二个 INSERT 会被
        `uq_applications_job` 拒掉，`insert_for_job` 转而返回先落库的那条。
        """
        import threading

        barrier = threading.Barrier(2, timeout=10)
        outcomes: list[object] = []
        lock = threading.Lock()

        def apply() -> None:
            barrier.wait()
            outcome = apply_to_job(store=store, job=_job(), now=NOW)
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=apply) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert len(outcomes) == 2
        ids = {outcome.application.id for outcome in outcomes}  # type: ignore[attr-defined]
        assert len(ids) == 1, f"并发下产生了多条投递：{ids}"
        assert [outcome.created for outcome in outcomes].count(True) == 1  # type: ignore[attr-defined]
        assert len(store.by_job("j1")) == 1

    def test_different_jobs_are_separate_records(self, store: ApplicationStore) -> None:
        """幂等只针对同一岗位 —— 不同岗位各记一条。"""
        first = apply_to_job(store=store, job=_job(id="j1"), now=NOW)
        second = apply_to_job(store=store, job=_job(id="j2"), now=NOW)
        assert first.application.id != second.application.id
        assert store.count() == 2
