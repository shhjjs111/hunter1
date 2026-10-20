"""sync 切片测试 —— 真 SQLite、假时钟、全程离线。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hunter1.domain.crawl import company_identity, job_identity
from hunter1.domain.models import Application, ApplicationStage, CaptureStatus, Job
from hunter1.platform.db import Database
from hunter1.slices.applications import ApplicationStore
from hunter1.slices.jobs import JobStore
from hunter1.slices.scoring import CandidateProfile, ScoreStore
from hunter1.slices.sync import SyncStore, build_router
from hunter1.slices.sync.schemas import SyncApplication, SyncJob, SyncProfile, SyncSnapshot

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)
LATEST = NOW + timedelta(hours=2)
URL = "https://example.com/jobs/1"


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "sync.db")
    database.initialize()
    return database


@pytest.fixture()
def jobs(db: Database) -> JobStore:
    return JobStore(db)


@pytest.fixture()
def applications(db: Database) -> ApplicationStore:
    return ApplicationStore(db)


@pytest.fixture()
def scores(db: Database) -> ScoreStore:
    return ScoreStore(db)


@pytest.fixture()
def store(jobs: JobStore, applications: ApplicationStore, scores: ScoreStore) -> SyncStore:
    return SyncStore(jobs=jobs, applications=applications, scores=scores, clock=lambda: NOW)


def _seed_job(
    db: Database,
    *,
    title: str = "AI产品经理",
    last_seen: datetime = NOW,
    source: str = "实习僧",
    capture_status: CaptureStatus = CaptureStatus.COMPLETE,
) -> str:
    job_id = job_identity(detail_url=URL)
    db.jobs().upsert(
        Job(
            id=job_id,
            company_id=company_identity("字节跳动"),
            title=title,
            detail_url=URL,
            source=source,
            company_name="字节跳动",
            city="北京",
            jd_raw="旧 JD",
            capture_status=capture_status,
            first_seen_at=NOW - timedelta(days=1),
            last_seen_at=last_seen,
        )
    )
    return job_id


def _seed_application(db: Database, job_id: str) -> None:
    db.applications().insert_for_job(
        Application(
            id="a-local",
            job_id=job_id,
            company="字节跳动",
            title="AI产品经理",
            stage=ApplicationStage.APPLIED,
            applied_at=NOW - timedelta(days=2),
            updated_at=NOW - timedelta(days=1),
        )
    )


def _job(**overrides: object) -> SyncJob:
    base: dict[str, object] = {
        "id": "mobile-job-id",
        "title": "AI产品经理",
        "company": "字节跳动",
        "city": "北京",
        "source": "实习僧",
        "detail_url": URL,
        "jd_raw": "新 JD",
        "capture_status": CaptureStatus.COMPLETE,
        "first_seen_at": NOW - timedelta(days=1),
        "last_seen_at": LATER,
    }
    base.update(overrides)
    return SyncJob(**base)  # type: ignore[arg-type]


def _snapshot(
    *,
    jobs: list[SyncJob] | None = None,
    applications: list[SyncApplication] | None = None,
    profile: SyncProfile | None = None,
) -> SyncSnapshot:
    return SyncSnapshot(
        exported_at=NOW,
        jobs=jobs or [],
        applications=applications or [],
        profile=profile,
    )


class TestExport:
    def test_exports_jobs_applications_and_profile(
        self, db: Database, store: SyncStore, scores: ScoreStore
    ) -> None:
        job_id = _seed_job(db)
        _seed_application(db, job_id)
        scores.save_profile_at(CandidateProfile(keywords=["AI"], summary="背景"), LATER)

        snapshot = store.export_snapshot()

        assert snapshot.version == 1
        assert [(job.detail_url, job.company) for job in snapshot.jobs] == [(URL, "字节跳动")]
        assert snapshot.applications[0].job_detail_url == URL
        assert snapshot.profile is not None
        assert snapshot.profile.keywords == ["AI"]
        assert snapshot.profile.updated_at == LATER

    def test_export_never_contains_api_key(self, db: Database, store: SyncStore) -> None:
        from hunter1.domain.settings import LLMSettings

        db.settings().save_llm(
            LLMSettings(base_url="https://api.example.com/v1", model="m", api_key="sk-secret")
        )
        _seed_job(db)
        rendered = store.export_snapshot().model_dump_json()
        assert "sk-secret" not in rendered


class TestMergeJobs:
    def test_new_job_is_created_by_detail_url_and_scored(
        self, store: SyncStore, db: Database
    ) -> None:
        incoming = _job(match_score=88, scored_at=LATER, score_summary="匹配")

        counts, snapshot = store.merge_snapshot(_snapshot(jobs=[incoming]))

        assert counts.jobs_created == 1
        created = db.jobs().get(job_identity(detail_url=URL))
        assert created is not None
        assert created.title == "AI产品经理"
        assert created.company_id == company_identity("字节跳动")
        assert created.match_score == 88
        assert created.score_summary == "匹配"
        assert snapshot.jobs[0].match_score == 88

    def test_new_job_without_first_seen_uses_last_seen(
        self, store: SyncStore, db: Database
    ) -> None:
        """手机不存 first_seen：导入时不能用服务器当前时间把它顶到 last_seen 之后。"""
        incoming = _job(first_seen_at=None)
        counts, snapshot = store.merge_snapshot(_snapshot(jobs=[incoming]))
        assert counts.jobs_created == 1
        created = db.jobs().get(job_identity(detail_url=URL))
        assert created is not None
        assert created.first_seen_at == created.last_seen_at
        assert snapshot.jobs[0].first_seen_at == snapshot.jobs[0].last_seen_at

    def test_newer_job_facts_win_and_older_does_not_roll_back(
        self, store: SyncStore, db: Database
    ) -> None:
        job_id = _seed_job(db, title="旧标题", last_seen=NOW)

        counts, _ = store.merge_snapshot(_snapshot(jobs=[_job(title="新标题", last_seen_at=LATER)]))
        assert counts.jobs_updated == 1
        assert db.jobs().get(job_id).title == "新标题"  # type: ignore[union-attr]

        counts, _ = store.merge_snapshot(_snapshot(jobs=[_job(title="更旧标题", last_seen_at=NOW)]))
        assert counts.jobs_kept == 1
        assert db.jobs().get(job_id).title == "新标题"  # type: ignore[union-attr]

    def test_score_only_moves_forward(self, store: SyncStore, db: Database) -> None:
        job_id = _seed_job(db)
        store.merge_snapshot(_snapshot(jobs=[_job(match_score=91, scored_at=LATER)]))

        counts, _ = store.merge_snapshot(_snapshot(jobs=[_job(match_score=20, scored_at=NOW)]))

        assert counts.jobs_kept == 1
        assert db.jobs().get(job_id).match_score == 91  # type: ignore[union-attr]

    def test_missing_jd_does_not_overwrite_existing_jd(
        self, store: SyncStore, db: Database
    ) -> None:
        job_id = _seed_job(db)
        store.merge_snapshot(_snapshot(jobs=[_job(jd_raw=None, last_seen_at=LATER)]))
        assert db.jobs().get(job_id).jd_raw == "旧 JD"  # type: ignore[union-attr]

    def test_older_jd_does_not_overwrite_newer_jd(self, store: SyncStore, db: Database) -> None:
        job_id = _seed_job(db)
        store.merge_snapshot(_snapshot(jobs=[_job(jd_raw="新 JD", last_seen_at=LATER)]))
        store.merge_snapshot(_snapshot(jobs=[_job(jd_raw="更旧的 JD", last_seen_at=NOW)]))
        assert db.jobs().get(job_id).jd_raw == "新 JD"  # type: ignore[union-attr]

    def test_older_snapshot_only_fills_blank_source_and_capture_status(
        self, store: SyncStore, db: Database
    ) -> None:
        job_id = _seed_job(db, source="", capture_status=CaptureStatus.UNKNOWN)

        counts, _ = store.merge_snapshot(
            _snapshot(
                jobs=[
                    _job(
                        source="实习僧",
                        capture_status=CaptureStatus.COMPLETE,
                        last_seen_at=NOW - timedelta(days=1),
                    )
                ]
            )
        )

        assert counts.jobs_updated == 1
        stored = db.jobs().get(job_id)
        assert stored is not None
        assert stored.source == "实习僧"
        assert stored.capture_status is CaptureStatus.COMPLETE

    def test_older_snapshot_does_not_override_present_source_or_capture_status(
        self, store: SyncStore, db: Database
    ) -> None:
        job_id = _seed_job(db, source="实习僧", capture_status=CaptureStatus.COMPLETE)

        counts, _ = store.merge_snapshot(
            _snapshot(
                jobs=[
                    _job(
                        source="另一个来源",
                        capture_status=CaptureStatus.FAILED,
                        last_seen_at=NOW - timedelta(days=1),
                    )
                ]
            )
        )

        assert counts.jobs_kept == 1
        stored = db.jobs().get(job_id)
        assert stored is not None
        assert stored.source == "实习僧"
        assert stored.capture_status is CaptureStatus.COMPLETE


class TestMergeApplications:
    def test_application_is_aligned_by_detail_url(self, store: SyncStore, db: Database) -> None:
        job_id = _seed_job(db)
        incoming = SyncApplication(
            job_detail_url=URL,
            title="AI产品经理",
            company="字节跳动",
            stage=ApplicationStage.INTERVIEW,
            note="二面",
            applied_at=NOW - timedelta(days=2),
            updated_at=LATER,
        )

        counts, _ = store.merge_snapshot(_snapshot(applications=[incoming]))

        assert counts.applications_created == 1
        records = db.applications().by_job(job_id)
        assert len(records) == 1
        assert records[0].stage is ApplicationStage.INTERVIEW
        assert records[0].note == "二面"

    def test_application_lww_and_old_update_is_ignored(
        self, store: SyncStore, db: Database
    ) -> None:
        job_id = _seed_job(db)
        _seed_application(db, job_id)
        older = SyncApplication(
            job_detail_url=URL,
            title="AI产品经理",
            company="字节跳动",
            stage=ApplicationStage.REJECTED,
            applied_at=NOW - timedelta(days=2),
            updated_at=NOW,
        )
        newer = SyncApplication(
            job_detail_url=URL,
            title="AI产品经理",
            company="字节跳动",
            stage=ApplicationStage.OFFER,
            applied_at=NOW - timedelta(days=2),
            updated_at=LATEST,
        )

        store.merge_snapshot(_snapshot(applications=[newer]))
        counts, _ = store.merge_snapshot(_snapshot(applications=[older]))

        assert counts.applications_kept == 1
        assert db.applications().by_job(job_id)[0].stage is ApplicationStage.OFFER

    def test_application_without_job_is_skipped(self, store: SyncStore) -> None:
        incoming = SyncApplication(
            job_detail_url="https://unknown.example/job/9",
            title="未知岗位",
            company="某公司",
            applied_at=NOW,
            updated_at=NOW,
        )
        counts, _ = store.merge_snapshot(_snapshot(applications=[incoming]))
        assert counts.applications_skipped == 1


class TestMergeProfile:
    def test_newer_profile_wins(self, db: Database, store: SyncStore, scores: ScoreStore) -> None:
        scores.save_profile_at(CandidateProfile(keywords=["旧"]), NOW)
        incoming = SyncProfile(keywords=["新"], updated_at=LATER)

        counts, snapshot = store.merge_snapshot(_snapshot(profile=incoming))

        assert counts.profile_updated is True
        assert scores.load_profile().keywords == ["新"]  # type: ignore[union-attr]
        assert snapshot.profile is not None and snapshot.profile.keywords == ["新"]

    def test_older_profile_is_kept(
        self, db: Database, store: SyncStore, scores: ScoreStore
    ) -> None:
        scores.save_profile_at(CandidateProfile(keywords=["新"]), LATEST)
        counts, _ = store.merge_snapshot(
            _snapshot(profile=SyncProfile(keywords=["旧"], updated_at=NOW))
        )
        assert counts.profile_updated is False
        assert scores.load_profile().keywords == ["新"]  # type: ignore[union-attr]


class TestRouter:
    def test_snapshot_and_merge_are_typed_endpoints(self, store: SyncStore) -> None:
        app = FastAPI()
        app.include_router(build_router(store=store), prefix="/api")
        incoming = _snapshot(jobs=[_job()])
        with TestClient(app) as client:
            exported = client.get("/api/sync/snapshot")
            assert exported.status_code == 200
            assert exported.json()["version"] == 1

            merged = client.post("/api/sync/merge", json=incoming.model_dump(mode="json"))
            assert merged.status_code == 200
            payload = merged.json()
            assert payload["counts"]["jobs_created"] == 1
            assert payload["snapshot"]["jobs"][0]["detail_url"] == URL

    def test_empty_profile_is_422_not_500(self, store: SyncStore) -> None:
        app = FastAPI()
        app.include_router(build_router(store=store), prefix="/api")
        empty = _snapshot(profile=SyncProfile(updated_at=NOW))
        with TestClient(app) as client:
            response = client.post("/api/sync/merge", json=empty.model_dump(mode="json"))
        assert response.status_code == 422
        assert "同步数据不合法" in response.json()["detail"]

    def test_invalid_profile_does_not_partially_merge_jobs(self, store: SyncStore) -> None:
        """坏画像必须在动任何一行之前被拒 —— 否则调用方看到失败、本机却被改了一半。"""
        app = FastAPI()
        app.include_router(build_router(store=store), prefix="/api")
        payload = _snapshot(
            jobs=[_job()],
            profile=SyncProfile(updated_at=NOW),
        )
        with TestClient(app) as client:
            response = client.post("/api/sync/merge", json=payload.model_dump(mode="json"))
            assert response.status_code == 422
            exported = client.get("/api/sync/snapshot").json()
        assert exported["jobs"] == []

    def test_mixed_timezones_are_422_not_500(self, store: SyncStore) -> None:
        app = FastAPI()
        app.include_router(build_router(store=store), prefix="/api")
        payload = {
            "version": 1,
            "exported_at": "2026-10-20T12:00:00Z",
            "jobs": [
                {
                    "id": "j",
                    "title": "岗位",
                    "company": "公司",
                    "detail_url": URL,
                    "first_seen_at": "2026-10-20T12:00:00",
                    "last_seen_at": "2026-10-20T12:00:00Z",
                }
            ],
        }
        with TestClient(app) as client:
            response = client.post("/api/sync/merge", json=payload)
        assert response.status_code == 422
