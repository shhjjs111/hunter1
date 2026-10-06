"""jobs 切片 HTTP 面测试 —— 真 SQLite + TestClient，全程离线。

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

from hunter1.domain.models import CaptureStatus, Job
from hunter1.platform.db import Database
from hunter1.slices.jobs.router import build_router
from hunter1.slices.jobs.store import JobStore

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
FULL_A = "a" * 32
FULL_B = "a" * 8 + "b" * 24
FULL_C = "c" * 32


def _seed(db: Database) -> None:
    repo = db.jobs()
    repo.upsert(
        Job(
            id=FULL_A,
            company_id="c1",
            title="AI产品经理",
            detail_url="https://x/1",
            source="实习僧",
            company_name="字节跳动",
            city="北京",
            match_score=88,
            capture_status=CaptureStatus.COMPLETE,
            jd_raw="负责大模型产品规划。",
            last_seen_at=datetime(2026, 10, 5, tzinfo=UTC),
        )
    )
    repo.upsert(
        Job(
            id=FULL_B,
            company_id="c2",
            title="大模型产品经理（2027校招）",
            detail_url="https://x/2",
            source="实习僧",
            company_name="某创业公司",
            last_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
        )
    )
    repo.upsert(
        Job(
            id=FULL_C,
            company_id="c3",
            title="行政专员",
            detail_url="https://x/3",
            source="实习僧",
            company_name="某国企",
            last_seen_at=datetime(2026, 10, 3, tzinfo=UTC),
        )
    )


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    db = Database(tmp_path / "jobs-api.db")
    db.initialize()
    _seed(db)
    return JobStore(db)


@pytest.fixture()
def client(store: JobStore) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(build_router(store=store, clock=lambda: NOW), prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


class TestListEndpoint:
    def test_ok_shape(self, client: TestClient) -> None:
        response = client.get("/api/jobs")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["page"] == 1
        assert payload["page_size"] == 20
        assert payload["has_next"] is False
        assert len(payload["items"]) == 3
        first = payload["items"][0]
        # 契约字段齐备且公司是**人读的名字**（不是身份哈希）
        assert first["company"] == "字节跳动"
        assert first["title"] == "AI产品经理"
        assert first["match_score"] == 88
        assert first["capture_status"] == "complete"

    def test_search(self, client: TestClient) -> None:
        payload = client.get("/api/jobs", params={"q": "产品经理"}).json()
        assert payload["total"] == 2

    def test_pagination(self, client: TestClient) -> None:
        payload = client.get("/api/jobs", params={"page_size": 2}).json()
        assert payload["has_next"] is True
        assert len(payload["items"]) == 2

    def test_out_of_range_params_rejected(self, client: TestClient) -> None:
        assert client.get("/api/jobs", params={"page_size": 101}).status_code == 422


class TestDetailEndpoint:
    def test_full_id(self, client: TestClient) -> None:
        response = client.get(f"/api/jobs/{FULL_A}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == FULL_A
        assert payload["jd_raw"] == "负责大模型产品规划。"

    def test_unique_prefix(self, client: TestClient) -> None:
        response = client.get(f"/api/jobs/{'a' * 8}b")
        assert response.status_code == 200
        assert response.json()["id"] == FULL_B

    def test_ambiguous_prefix_is_409(self, client: TestClient) -> None:
        response = client.get(f"/api/jobs/{'a' * 8}")
        assert response.status_code == 409
        assert "2 条匹配" in response.json()["detail"]

    def test_missing_is_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/zzzz").status_code == 404


class TestApplyEndpoint:
    def test_201_and_persisted(self, client: TestClient, store: JobStore) -> None:
        response = client.post(f"/api/jobs/{FULL_A}/apply")
        assert response.status_code == 201
        application_id = response.json()["application_id"]
        assert store._db.applications().get(application_id) is not None

    def test_missing_job_is_404(self, client: TestClient) -> None:
        assert client.post("/api/jobs/zzzz/apply").status_code == 404

    def test_repeat_apply_returns_same_id(self, client: TestClient, store: JobStore) -> None:
        """重复点击投递：响应幂等（同一 application_id），且库里只有一条记录。"""
        first = client.post(f"/api/jobs/{FULL_A}/apply").json()["application_id"]
        second = client.post(f"/api/jobs/{FULL_A}/apply").json()["application_id"]
        assert second == first
        assert len(store._db.applications().by_job(FULL_A)) == 1
