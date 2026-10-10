"""jobs 切片 HTTP 面测试 —— 真 SQLite + TestClient，全程离线。

只挂本切片的 router（不带 web 层），验证它作为独立单元能跑 ——
这正是「切片可独立验证」的判据。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hunter1.domain.models import CaptureStatus, Job
from hunter1.platform.db import Database
from hunter1.slices.jobs.router import build_router
from hunter1.slices.jobs.schemas import JobSummary
from hunter1.slices.jobs.store import JobStore

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
FULL_A = "a" * 32
FULL_B = "a" * 8 + "b" * 24
FULL_C = "c" * 32


def test_capture_status_is_a_real_enum_in_the_contract() -> None:
    """契约里必须是**真 enum**，不是裸 `string`。

    裸 `str` 时前端只能维护一份手抄的取值表，取值一变两边就漂 ——
    `applications/schemas.py` 的 `stage` 早就用枚举，注释里也写明了理由，jobs 这边
    原先漏了。这条读的是**契约本身**（Pydantic 生成给 OpenAPI 的那份 schema），
    所以把字段改回 `str` 会直接红。
    """
    schema = JobSummary.model_json_schema()
    rendered = json.dumps(schema["properties"]["capture_status"])
    assert "CaptureStatus" in rendered, f"capture_status 在契约里不是 enum：{rendered}"

    values = schema["$defs"]["CaptureStatus"]["enum"]
    assert set(values) == {"unknown", "pending", "complete", "failed"}, values


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
    app.include_router(build_router(store=store), prefix="/api")
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

    def test_page_upper_bound_is_rejected_not_silently_clamped(self, client: TestClient) -> None:
        """页码同样要有上界，且越界由 **422** 拒绝 —— 不静默改成第 10000 页。

        原先 `page` 只有 `ge=1`、没有 `le`，而服务层 `min(max(1, page), MAX_PAGE)`
        会悄悄钳制：传 999999 得 200 且响应里 `page=10000`，调用方从契约与响应都
        看不出自己的入参被改过。同端点的 `page_size` 早就有 `le` —— 两个分页参数
        必须对称。
        """
        assert client.get("/api/jobs", params={"page": 10_001}).status_code == 422
        assert client.get("/api/jobs", params={"page": 0}).status_code == 422
        # 边界值本身仍然可用
        assert client.get("/api/jobs", params={"page": 10_000}).status_code == 200


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
