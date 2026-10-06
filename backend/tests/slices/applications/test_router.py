"""applications 切片 HTTP 面测试 —— 真 SQLite + TestClient，全程离线。

只挂本切片的 router（不带 web 层），验证它作为独立单元能跑 ——
这正是「切片可独立验证」的判据。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hunter1.domain.models import Application, ApplicationStage, Job
from hunter1.platform.db import Database
from hunter1.slices.applications.router import build_router
from hunter1.slices.applications.store import ApplicationStore
from hunter1.slices.jobs import JobStore

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
JOB_FULL = "d" * 32
JOB_PREFIX_TWIN = "d" * 8 + "e" * 24


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "applications-api.db")
    database.initialize()
    return database


@pytest.fixture()
def store(db: Database) -> ApplicationStore:
    return ApplicationStore(db)


@pytest.fixture()
def jobs(db: Database) -> JobStore:
    """记录投递要按 id 查岗位 —— 与 store 共用同一个库。"""
    return JobStore(db)


@pytest.fixture()
def client(store: ApplicationStore, jobs: JobStore) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(build_router(store=store, jobs=jobs, clock=lambda: NOW), prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


def _seed_job(db: Database, job_id: str = JOB_FULL) -> None:
    db.jobs().upsert(
        Job(
            id=job_id,
            company_id="c1",
            title="AI产品经理",
            detail_url="https://x/1",
            source="实习僧",
            company_name="字节跳动",
        )
    )


def _application(**overrides: object) -> Application:
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


class TestListEndpoint:
    def test_empty(self, client: TestClient) -> None:
        response = client.get("/api/applications")
        assert response.status_code == 200
        assert response.json() == {"items": []}

    def test_lists_recent_first_with_shape(
        self, client: TestClient, store: ApplicationStore
    ) -> None:
        store.upsert(_application(id="old", updated_at=datetime(2026, 9, 2, tzinfo=UTC)))
        store.upsert(
            _application(
                id="new",
                stage=ApplicationStage.INTERVIEW,
                updated_at=datetime(2026, 10, 2, tzinfo=UTC),
            )
        )
        payload = client.get("/api/applications").json()
        assert [item["id"] for item in payload["items"]] == ["new", "old"]
        first = payload["items"][0]
        # 契约字段齐备，且 stage 是字面值、公司是**人读的名字**
        assert first["company"] == "字节跳动"
        assert first["title"] == "AI产品经理"
        assert first["stage"] == "interview"
        assert first["job_id"] == "j1"


class TestStageEndpoint:
    def test_advances_and_persists(self, client: TestClient, store: ApplicationStore) -> None:
        store.upsert(_application(id="a1"))
        response = client.post("/api/applications/a1/stage", json={"stage": "interview"})
        assert response.status_code == 200
        assert response.json() == {"application_id": "a1", "stage": "interview"}
        loaded = store.get("a1")
        assert loaded is not None
        assert loaded.stage is ApplicationStage.INTERVIEW
        assert loaded.updated_at == NOW

    def test_unknown_stage_is_422(self, client: TestClient, store: ApplicationStore) -> None:
        """`stage` 现在是枚举类型 —— 非法值由请求体校验拒为 422（而非路由手工判 400）。"""
        store.upsert(_application(id="a1"))
        response = client.post("/api/applications/a1/stage", json={"stage": "bogus"})
        assert response.status_code == 422

    def test_missing_is_404(self, client: TestClient) -> None:
        response = client.post("/api/applications/zzzz/stage", json={"stage": "interview"})
        assert response.status_code == 404


class TestApplyEndpoint:
    """记录投递（入口从 jobs 迁到 applications —— 投递记录本体归本切片）。"""

    def test_creates_and_returns_201(
        self, client: TestClient, db: Database, store: ApplicationStore
    ) -> None:
        _seed_job(db)
        response = client.post("/api/applications", json={"job_id": JOB_FULL})
        assert response.status_code == 201
        application_id = response.json()["application_id"]
        assert store.get(application_id) is not None

    def test_accepts_unique_prefix(self, client: TestClient, db: Database) -> None:
        _seed_job(db)
        response = client.post("/api/applications", json={"job_id": JOB_FULL[:8]})
        assert response.status_code == 201

    def test_ambiguous_prefix_is_409(self, client: TestClient, db: Database) -> None:
        _seed_job(db, JOB_FULL)
        _seed_job(db, JOB_PREFIX_TWIN)
        response = client.post("/api/applications", json={"job_id": "d" * 8})
        assert response.status_code == 409
        assert "2 条匹配" in response.json()["detail"]

    def test_missing_job_is_404(self, client: TestClient) -> None:
        assert client.post("/api/applications", json={"job_id": "zzzz"}).status_code == 404

    def test_repeat_apply_returns_same_id(
        self, client: TestClient, db: Database, store: ApplicationStore
    ) -> None:
        """重复点击投递：响应幂等（同一 application_id），库里只有一条记录。"""
        _seed_job(db)
        first = client.post("/api/applications", json={"job_id": JOB_FULL}).json()["application_id"]
        second = client.post("/api/applications", json={"job_id": JOB_FULL}).json()[
            "application_id"
        ]
        assert second == first
        assert len(store.by_job(JOB_FULL)) == 1


class TestDeleteEndpoint:
    def test_delete_returns_204_and_removes(
        self, client: TestClient, store: ApplicationStore
    ) -> None:
        store.upsert(_application(id="a1"))
        response = client.delete("/api/applications/a1")
        assert response.status_code == 204
        assert store.get("a1") is None
        assert store.count() == 0

    def test_delete_missing_is_204(self, client: TestClient) -> None:
        assert client.delete("/api/applications/zzzz").status_code == 204
