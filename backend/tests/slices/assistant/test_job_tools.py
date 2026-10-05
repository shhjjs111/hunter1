"""真实工具集测试 —— 用真实 SQLite，离线。

TDD：本文件先于实现编写（实现已随 M4c 一并落地，此处覆盖行为）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter1.domain.models import ApplicationStage, CaptureStatus, Job
from hunter1.platform.db import Database
from hunter1.slices.applications import change_stage, new_application
from hunter1.slices.assistant.job_tools import build_tools


@pytest.fixture()
def jobs_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "tools.db")
    db.initialize()
    repo = db.jobs()
    repo.upsert(
        Job(
            id="j1" + "0" * 30,
            company_id="c1",
            title="AI产品经理",
            detail_url="https://a.com/1",
            source="t",
            city="北京",
            match_score=88,
            capture_status=CaptureStatus.COMPLETE,
            jd_raw="负责大模型产品规划。",
            last_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )
    repo.upsert(
        Job(
            id="j2" + "0" * 30,
            company_id="c1",
            title="行政专员",
            detail_url="https://a.com/2",
            source="t",
            last_seen_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    return db


class TestSearchJobsTool:
    def test_finds_matching_jobs(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs())
        result = registry.invoke("search_jobs", {"keyword": "产品经理"}, call_id="c1")
        assert result.ok
        assert "AI产品经理" in result.content
        assert "行政专员" not in result.content
        assert "88" in result.content  # 带上了匹配分

    def test_no_match_message_is_clear(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs())
        result = registry.invoke("search_jobs", {"keyword": "律师"}, call_id="c1")
        assert result.ok
        assert "没有找到" in result.content

    def test_empty_keyword_lists_recent(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs())
        result = registry.invoke("search_jobs", {}, call_id="c1")
        assert result.ok
        assert "AI产品经理" in result.content

    def test_limit_is_capped(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs())
        result = registry.invoke("search_jobs", {"keyword": "", "limit": 9999}, call_id="c1")
        assert result.ok  # 不应因越界参数而报错


class TestJobDetailTool:
    def test_full_id(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs())
        result = registry.invoke("job_detail", {"job_id": "j1" + "0" * 30}, call_id="c1")
        assert result.ok
        assert "AI产品经理" in result.content
        assert "负责大模型产品规划" in result.content
        assert "88" in result.content

    def test_id_prefix(self, jobs_db: Database) -> None:
        """模型常只看到前 8 位 id。"""
        registry = build_tools(jobs=jobs_db.jobs())
        result = registry.invoke("job_detail", {"job_id": "j1"}, call_id="c1")
        assert result.ok
        assert "AI产品经理" in result.content

    def test_missing_job(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs())
        result = registry.invoke("job_detail", {"job_id": "nope"}, call_id="c1")
        assert result.ok  # 「找不到」是正常结果而非工具错误
        assert "找不到" in result.content


class TestJobDetailPrefixLookup:
    """前缀查找必须下推到 SQL —— 不能在「最近 500 条」里碰运气。"""

    def test_prefix_lookup_reaches_beyond_recent_window(self, tmp_path: Path) -> None:
        """目标岗位排在最近窗口之外时也要找得到（旧实现只扫 list(500) 会漏）。"""
        db = Database(tmp_path / "big.db")
        db.initialize()
        repo = db.jobs()
        old = datetime(2020, 1, 1, tzinfo=UTC)
        new = datetime(2026, 10, 1, tzinfo=UTC)
        # 目标：id 前缀唯一，且是库里最老的一条（一定排进「最近 500 条」之外）
        target_id = "f0f0" + "a" * 60
        repo.upsert(
            Job(
                id=target_id,
                company_id="c",
                title="远古岗位",
                detail_url="https://x/0",
                source="t",
                last_seen_at=old,
            )
        )
        for index in range(500):
            repo.upsert(
                Job(
                    id=f"{index:064x}",
                    company_id="c",
                    title=f"岗位{index}",
                    detail_url=f"https://x/{index}",
                    source="t",
                    last_seen_at=new,
                )
            )
        result = build_tools(jobs=repo).invoke("job_detail", {"job_id": "f0f0"}, call_id="c1")
        assert result.ok
        assert "远古岗位" in result.content

    def test_ambiguous_prefix_reports_count(self, jobs_db: Database) -> None:
        """多个前缀命中时提示更长的 id，而不是随便挑一条。"""
        repo = jobs_db.jobs()
        repo.upsert(
            Job(
                id="p1" + "0" * 30,
                company_id="c",
                title="甲岗",
                detail_url="https://x/a",
                source="t",
            )
        )
        repo.upsert(
            Job(
                id="p2" + "0" * 30,
                company_id="c",
                title="乙岗",
                detail_url="https://x/b",
                source="t",
            )
        )
        result = build_tools(jobs=repo).invoke("job_detail", {"job_id": "p"}, call_id="c1")
        assert result.ok
        assert "2 条匹配" in result.content


class TestJobStatsTool:
    def test_reports_total(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs())
        result = registry.invoke("job_stats", {}, call_id="c1")
        assert result.ok
        assert "2" in result.content


class TestApplicationQueryTool:
    """助手能查投递记录 —— 需要装配了投递仓储才有这个工具。"""

    def _registry(self, jobs_db: Database):
        return build_tools(jobs=jobs_db.jobs(), applications=jobs_db.applications())

    def _seed(self, jobs_db: Database) -> None:
        job = jobs_db.jobs().get("j1" + "0" * 30)
        assert job is not None
        application = new_application(
            job=job, now=datetime(2026, 10, 1, tzinfo=UTC), application_id="a1"
        )
        application = change_stage(
            application,
            stage=ApplicationStage.INTERVIEW,
            now=datetime(2026, 10, 4, tzinfo=UTC),
            note="二面",
        )
        jobs_db.applications().upsert(application)

    def test_reports_applications_with_stage(self, jobs_db: Database) -> None:
        self._seed(jobs_db)
        result = self._registry(jobs_db).invoke("application_query", {}, call_id="c1")
        assert result.ok
        assert "AI产品经理" in result.content
        assert "面试" in result.content

    def test_empty_library_says_so(self, jobs_db: Database) -> None:
        result = self._registry(jobs_db).invoke("application_query", {}, call_id="c1")
        assert result.ok
        assert "还没有投递" in result.content

    def test_can_filter_by_job_id(self, jobs_db: Database) -> None:
        self._seed(jobs_db)
        found = self._registry(jobs_db).invoke(
            "application_query", {"job_id": "j1" + "0" * 30}, call_id="c1"
        )
        assert found.ok and "AI产品经理" in found.content
        missing = self._registry(jobs_db).invoke(
            "application_query", {"job_id": "nope"}, call_id="c1"
        )
        assert missing.ok and "没有查到" in missing.content


class TestToolSetShape:
    def test_registry_exposes_three_tools(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs())
        assert registry.names() == ["job_detail", "job_stats", "search_jobs"]

    def test_application_tool_appears_only_when_wired(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs(), applications=jobs_db.applications())
        assert "application_query" in registry.names()

    def test_tools_are_read_only(self, jobs_db: Database) -> None:
        """工具集里不该出现写操作（写由用户在界面确认）。"""
        registry = build_tools(jobs=jobs_db.jobs())
        forbidden = ("delete", "update", "create", "remove", "write", "apply")
        for name in registry.names():
            assert not any(word in name for word in forbidden), f"{name} 看起来是写操作"

    def test_unknown_tool_reports_available(self, jobs_db: Database) -> None:
        registry = build_tools(jobs=jobs_db.jobs())
        result = registry.invoke("ghost", {}, call_id="c1")
        assert not result.ok
        assert "search_jobs" in (result.error or "")
