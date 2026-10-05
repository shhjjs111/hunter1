"""投递用例单元测试 —— 纯领域逻辑，无 IO。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hunter1.application.applications import change_stage, new_application
from hunter1.domain.models import ApplicationStage, Job


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
