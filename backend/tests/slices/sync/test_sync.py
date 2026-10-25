"""sync 切片测试 —— 真 SQLite、假时钟、全程离线。"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hunter1.domain.crawl import company_identity, job_identity
from hunter1.domain.models import Application, ApplicationStage, CaptureStatus, Job
from hunter1.platform.db import Database
from hunter1.slices.applications import ApplicationStore
from hunter1.slices.jobs import JobStore
from hunter1.slices.scoring import CandidateProfile, ScoreStore
from hunter1.slices.sync import SyncBodyLimitMiddleware, SyncStore, build_router
from hunter1.slices.sync import router as sync_router
from hunter1.slices.sync.schemas import (
    MAX_JD_CHARS,
    SyncApplicationImport,
    SyncJobImport,
    SyncProfile,
    SyncSnapshotImport,
)

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
def store(
    db: Database, jobs: JobStore, applications: ApplicationStore, scores: ScoreStore
) -> SyncStore:
    return SyncStore(
        db=db,
        jobs=jobs,
        applications=applications,
        scores=scores,
        clock=lambda: NOW,
    )


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


def _job(**overrides: object) -> SyncJobImport:
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
    return SyncJobImport(**base)  # type: ignore[arg-type]


def _snapshot(
    *,
    jobs: list[SyncJobImport] | None = None,
    applications: list[SyncApplicationImport] | None = None,
    profile: SyncProfile | None = None,
) -> SyncSnapshotImport:
    return SyncSnapshotImport(
        exported_at=NOW,
        jobs=jobs or [],
        applications=applications or [],
        profile=profile,
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_urls_keep_independent_fact_and_score_versions(
    store: SyncStore, reverse: bool
) -> None:
    items = [
        _job(title="新事实", last_seen_at=LATER, match_score=20, scored_at=NOW),
        _job(
            title="旧事实",
            last_seen_at=NOW,
            match_score=91,
            scored_at=LATER,
            score_summary="新结论",
        ),
    ]
    if reverse:
        items.reverse()
    counts, snapshot = store.merge_snapshot(_snapshot(jobs=items))
    assert counts.jobs_created == 1
    assert snapshot.jobs[0].title == "新事实"
    assert snapshot.jobs[0].match_score == 91
    assert snapshot.jobs[0].score_summary == "新结论"


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
        incoming = SyncApplicationImport(
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
        older = SyncApplicationImport(
            job_detail_url=URL,
            title="AI产品经理",
            company="字节跳动",
            stage=ApplicationStage.REJECTED,
            applied_at=NOW - timedelta(days=2),
            updated_at=NOW,
        )
        newer = SyncApplicationImport(
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
        incoming = SyncApplicationImport(
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


class TestExportTolerance:
    def test_oversize_jd_and_empty_url_still_export(self, db: Database, store: SyncStore) -> None:
        db.jobs().upsert(
            Job(
                id="long-jd",
                company_id=company_identity("某公司"),
                title="超长 JD",
                detail_url="https://example.com/jobs/long",
                source="某公司",
                jd_raw="x" * (MAX_JD_CHARS + 1),
                capture_status=CaptureStatus.COMPLETE,
                first_seen_at=NOW,
                last_seen_at=NOW,
            )
        )
        db.jobs().upsert(
            Job(
                id="no-url",
                company_id=company_identity("某公司"),
                title="没有详情链接",
                detail_url="",
                source="某公司",
                capture_status=CaptureStatus.PENDING,
                first_seen_at=NOW,
                last_seen_at=NOW,
            )
        )

        snapshot = store.export_snapshot()

        exported = {job.id: job for job in snapshot.jobs}
        assert exported["long-jd"].jd_raw is not None
        assert len(exported["long-jd"].jd_raw) == MAX_JD_CHARS + 1
        assert exported["no-url"].detail_url == ""

    def test_corrupt_profile_is_exported_as_missing(self, db: Database, store: SyncStore) -> None:
        from hunter1.slices.scoring.store import PROFILE_KEY

        db.settings().set_raw(PROFILE_KEY, {"keywords": ["AI"], "summary": 123})

        assert store.export_snapshot().profile is None


class TestMergeAtomicity:
    def test_failure_mid_merge_rolls_back_every_write(
        self, db: Database, store: SyncStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = store._jobs.upsert_facts
        calls = 0

        def fail_on_second(job: Job, *, session: object = None) -> None:
            nonlocal calls
            calls += 1
            original(job, session=session)  # type: ignore[arg-type]
            if calls == 2:
                raise RuntimeError("boom")

        monkeypatch.setattr(store._jobs, "upsert_facts", fail_on_second)
        first = _job(detail_url="https://example.com/jobs/a", id="a")
        second = _job(detail_url="https://example.com/jobs/b", id="b")

        with pytest.raises(RuntimeError, match="boom"):
            store.merge_snapshot(_snapshot(jobs=[first, second]))

        assert db.jobs().count() == 0


class TestMergeConcurrency:
    def test_two_merges_are_serialized_without_lock_errors(
        self, db: Database, store: SyncStore
    ) -> None:
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def merge(index: int) -> None:
            try:
                barrier.wait()
                store.merge_snapshot(
                    _snapshot(
                        jobs=[
                            _job(
                                id=f"remote-{index}",
                                detail_url=f"https://example.com/jobs/{index}",
                            )
                        ]
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=merge, args=(index,)) for index in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert db.jobs().count() == 2


class TestImportValidation:
    """导出可以宽容历史数据，导入不能把不可写进库的字段放过去。"""

    @pytest.mark.parametrize(
        "field",
        ["title", "company", "detail_url", "jd_raw", "score_summary"],
    )
    def test_job_rejects_fields_that_cannot_be_written(self, field: str) -> None:
        value = {
            "title": "",
            "company": "",
            "detail_url": "",
            "jd_raw": "x" * (MAX_JD_CHARS + 1),
            "score_summary": "x" * 2001,
        }[field]
        with pytest.raises(ValidationError):
            _job(**{field: value})

    @pytest.mark.parametrize(
        "field",
        ["title", "company", "note"],
    )
    def test_application_rejects_fields_that_cannot_be_written(self, field: str) -> None:
        values: dict[str, object] = {
            "job_detail_url": URL,
            "title": "AI产品经理",
            "company": "字节跳动",
            "applied_at": NOW,
            "updated_at": LATER,
        }
        values[field] = "" if field in {"title", "company"} else "x" * 2001
        with pytest.raises(ValidationError):
            SyncApplicationImport(**values)  # type: ignore[arg-type]


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

    def test_too_many_jobs_is_422(self, store: SyncStore, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sync_router, "MAX_SYNC_JOBS", 1)
        app = FastAPI()
        app.include_router(build_router(store=store), prefix="/api")
        payload = _snapshot(
            jobs=[
                _job(id="a", detail_url="https://example.com/jobs/a"),
                _job(id="b", detail_url="https://example.com/jobs/b"),
            ]
        )
        with TestClient(app) as client:
            response = client.post("/api/sync/merge", json=payload.model_dump(mode="json"))
        assert response.status_code == 422
        assert "条目过多" in response.json()["detail"]

    def test_oversized_body_is_413(self, store: SyncStore) -> None:
        app = FastAPI()
        app.include_router(build_router(store=store), prefix="/api")
        with TestClient(app) as client:
            response = client.post(
                "/api/sync/merge",
                content=b"{}",
                headers={"content-length": str(sync_router.MAX_SYNC_BODY_BYTES + 1)},
            )
        assert response.status_code == 413

    def test_chunked_body_is_limited_without_content_length(self, store: SyncStore) -> None:
        """分块请求没有 Content-Length，也要在读取过程中按实际字节数拦截。"""
        app = FastAPI()
        app.add_middleware(SyncBodyLimitMiddleware, max_bytes=64)
        app.include_router(build_router(store=store), prefix="/api")

        def chunks():
            yield b'{"version": 1, "exported_at": "2026-10-20T12:00:00Z", '
            yield b'"jobs": [], "applications": [], "profile": null}'

        with TestClient(app) as client:
            response = client.post(
                "/api/sync/merge",
                content=chunks(),
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 413
        assert "上限" in response.json()["detail"]

    def test_chunked_body_under_limit_still_merges(self, store: SyncStore) -> None:
        app = FastAPI()
        app.add_middleware(SyncBodyLimitMiddleware, max_bytes=1024)
        app.include_router(build_router(store=store), prefix="/api")
        payload = _snapshot(jobs=[_job()]).model_dump(mode="json")

        def chunks():
            raw = json.dumps(payload).encode()
            yield raw[:20]
            yield raw[20:]

        with TestClient(app) as client:
            response = client.post(
                "/api/sync/merge",
                content=chunks(),
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 200
        assert response.json()["counts"]["jobs_created"] == 1

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
