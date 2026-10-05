"""domain.models 单元测试 —— 纯模型，无 IO。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hunter1.domain.models import CaptureStatus, Company, Job


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
