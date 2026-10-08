"""domain.models 单元测试 —— 纯模型，无 IO。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hunter1.domain.models import Application, ApplicationStage, CaptureStatus, Company, Job


class TestCompany:
    def test_minimal_construction(self) -> None:
        company = Company(id="c1", name="字节跳动", source="offerbiu")
        assert company.name == "字节跳动"
        assert company.aliases == []
        assert company.campus_url is None

    def test_default_list_is_not_shared(self) -> None:
        """可变默认值不得在实例间共享（经典陷阱）。"""
        first = Company(id="c1", name="A", source="s")
        second = Company(id="c2", name="B", source="s")
        first.aliases.append("别名")
        assert second.aliases == []


class TestJob:
    def _job(self, **overrides: object) -> Job:
        base: dict[str, object] = {
            "id": "j1",
            "company_id": "c1",
            "title": "AI产品经理（2027校招）",
            "detail_url": "https://example.com/job/1",
            "source": "offerbiu",
        }
        base.update(overrides)
        return Job(**base)

    def test_title_key_is_derived_from_title(self) -> None:
        """title_key 不是独立输入，而是 title 的归一化投影。"""
        job = self._job()
        assert job.title_key == "ai产品经理"

    def test_capture_status_defaults_to_unknown(self) -> None:
        assert self._job().capture_status is CaptureStatus.UNKNOWN

    def test_company_name_is_optional_display_field(self) -> None:
        """`company_id` 是身份哈希，人读不懂；界面要的公司名单独存。"""
        assert self._job().company_name is None
        assert self._job(company_name="字节跳动").company_name == "字节跳动"

    def test_invalid_capture_status_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._job(capture_status="not-a-status")

    def test_match_score_accepts_none_and_range(self) -> None:
        assert self._job().match_score is None
        assert self._job(match_score=0).match_score == 0
        assert self._job(match_score=100).match_score == 100

    @pytest.mark.parametrize("bad", [-1, 101])
    def test_match_score_out_of_range_is_rejected(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            self._job(match_score=bad)

    def test_first_seen_before_last_seen_is_enforced(self) -> None:
        """不变量：首次出现不可能晚于最后出现。"""
        earlier = datetime(2026, 9, 1, tzinfo=UTC)
        later = datetime(2026, 10, 1, tzinfo=UTC)
        # 正常顺序
        assert self._job(first_seen_at=earlier, last_seen_at=later).last_seen_at == later
        # 颠倒应被拒绝
        with pytest.raises(ValidationError):
            self._job(first_seen_at=later, last_seen_at=earlier)

    def test_mixed_timezone_awareness_is_a_validation_error(self) -> None:
        """L11：naive 与 aware 混用要报 ValidationError（→ 422），不是裸 TypeError（→ 500）。

        裸比较 `datetime > datetime` 在 awareness 不一致时抛 TypeError，
        pydantic 不把它包成 ValidationError —— 调用方按 ValidationError 处理会漏掉。
        """
        with pytest.raises(ValidationError):
            self._job(
                first_seen_at=datetime(2026, 9, 1),  # naive
                last_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
            )


class TestApplication:
    """投递记录 —— 岗位库之外的「我投了什么」。"""

    def _application(self, **overrides: object) -> Application:
        base: dict[str, object] = {
            "id": "a1",
            "job_id": "j1",
            "company": "字节跳动",
            "title": "AI产品经理",
            "applied_at": datetime(2026, 9, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 9, 2, tzinfo=UTC),
        }
        base.update(overrides)
        return Application(**base)  # type: ignore[arg-type]

    def test_defaults_to_applied_stage(self) -> None:
        assert self._application().stage is ApplicationStage.APPLIED

    def test_stage_accepts_known_values(self) -> None:
        assert self._application(stage="interview").stage is ApplicationStage.INTERVIEW

    def test_invalid_stage_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._application(stage="nonsense")

    def test_updated_at_cannot_precede_applied_at(self) -> None:
        """不变量：投递时间不可能晚于最后更新时间。"""
        with pytest.raises(ValidationError):
            self._application(
                applied_at=datetime(2026, 10, 1, tzinfo=UTC),
                updated_at=datetime(2026, 9, 1, tzinfo=UTC),
            )

    def test_note_is_optional(self) -> None:
        assert self._application().note is None

    def test_mixed_timezone_awareness_is_a_validation_error(self) -> None:
        """L11：同上，applied/updated 混用 aware 与 naive 也要是 ValidationError。"""
        with pytest.raises(ValidationError):
            self._application(
                applied_at=datetime(2026, 9, 1),  # naive
                updated_at=datetime(2026, 9, 2, tzinfo=UTC),
            )

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._application(salary="20k")
