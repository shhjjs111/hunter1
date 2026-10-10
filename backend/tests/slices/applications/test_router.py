"""applications 切片 HTTP 面测试 —— 真 SQLite + TestClient，全程离线。

只挂本切片的 router（不带 web 层），验证它作为独立单元能跑 ——
这正是「切片可独立验证」的判据。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from hunter1.domain.models import Application, ApplicationStage, Job
from hunter1.platform.db import Database
from hunter1.platform.db.applications import SqliteApplicationRepository
from hunter1.slices.applications.router import build_router
from hunter1.slices.applications.schemas import MAX_NOTE_CHARS
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
def seed(db: Database) -> SqliteApplicationRepository:
    """播种用：直接拿**平台仓储**。

    切片门面只暴露生产真正走的写路径（`insert_for_job`）—— 播种要能写任意 id 和
    `updated_at`（排序用例靠它），那是仓储的能力，不该为了测试留在门面上。
    """
    return db.applications()


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
        assert response.json() == {"items": [], "total": 0, "has_more": False}

    def test_lists_recent_first_with_shape(
        self,
        client: TestClient,
        store: ApplicationStore,
        seed: SqliteApplicationRepository,
    ) -> None:
        seed.upsert(
            _application(id="old", job_id="j-old", updated_at=datetime(2026, 9, 2, tzinfo=UTC))
        )
        seed.upsert(
            _application(
                id="new",
                job_id="j-new",
                stage=ApplicationStage.INTERVIEW,
                updated_at=datetime(2026, 10, 2, tzinfo=UTC),
            )
        )
        payload = client.get("/api/applications").json()
        assert [item["id"] for item in payload["items"]] == ["new", "old"]
        assert payload["total"] == 2
        assert payload["has_more"] is False
        first = payload["items"][0]
        # 契约字段齐备，且 stage 是字面值、公司是**人读的名字**
        assert first["company"] == "字节跳动"
        assert first["title"] == "AI产品经理"
        assert first["stage"] == "interview"
        assert first["job_id"] == "j-new"

    def test_truncation_is_reported_not_silent(
        self,
        client: TestClient,
        store: ApplicationStore,
        monkeypatch: pytest.MonkeyPatch,
        seed: SqliteApplicationRepository,
    ) -> None:
        """超过上限时必须说「还有更多」——否则第 N+1 条起永久不可见且看不出来。"""
        import hunter1.slices.applications.router as router_module

        monkeypatch.setattr(router_module, "LIST_LIMIT", 2)
        for index in range(3):
            seed.upsert(
                _application(
                    id=f"a{index}",
                    job_id=f"j{index}",
                    updated_at=datetime(2026, 9, 1 + index, tzinfo=UTC),
                )
            )
        payload = client.get("/api/applications").json()
        assert len(payload["items"]) == 2
        assert payload["total"] == 3
        assert payload["has_more"] is True


class TestStageEndpoint:
    def test_advances_and_persists(
        self, client: TestClient, store: ApplicationStore, seed: SqliteApplicationRepository
    ) -> None:
        seed.upsert(_application(id="a1"))
        response = client.post("/api/applications/a1/stage", json={"stage": "interview"})
        assert response.status_code == 200
        assert response.json() == {"application_id": "a1", "stage": "interview"}
        loaded = store.get("a1")
        assert loaded is not None
        assert loaded.stage is ApplicationStage.INTERVIEW
        assert loaded.updated_at == NOW

    def test_unknown_stage_is_422(
        self, client: TestClient, store: ApplicationStore, seed: SqliteApplicationRepository
    ) -> None:
        """`stage` 现在是枚举类型 —— 非法值由请求体校验拒为 422（而非路由手工判 400）。"""
        seed.upsert(_application(id="a1"))
        response = client.post("/api/applications/a1/stage", json={"stage": "bogus"})
        assert response.status_code == 422

    def test_missing_is_404(self, client: TestClient) -> None:
        response = client.post("/api/applications/zzzz/stage", json={"stage": "interview"})
        assert response.status_code == 404

    def test_deleted_mid_flight_is_not_resurrected(
        self,
        client: TestClient,
        store: ApplicationStore,
        monkeypatch: pytest.MonkeyPatch,
        seed: SqliteApplicationRepository,
    ) -> None:
        """get 与写入之间记录被删掉 → 404，且**不把记录插回去**。

        原实现是 `get` 后 `upsert`，而 upsert 见不到行就 INSERT —— 用户刚删掉的记录
        会在这次（可能来自另一个标签页的）阶段变更里悄悄复活，看起来像「删不掉」。
        """
        seed.upsert(_application(id="a1"))

        original = store.get

        def racing_get(application_id: str):
            loaded = original(application_id)
            store.delete(application_id)  # 读到之后、写入之前被删
            return loaded

        monkeypatch.setattr(store, "get", racing_get)
        response = client.post("/api/applications/a1/stage", json={"stage": "interview"})

        assert response.status_code == 404
        assert store.get("a1") is None, "记录被删后不该被阶段变更插回来"


class TestStageUpdateConflict:
    """阶段推进是**读-改-写**：并发下必须有人失败，而不是两边都 200 而有一边白改。"""

    def test_concurrent_change_is_409_not_a_silent_overwrite(
        self,
        db: Database,
        store: ApplicationStore,
        monkeypatch: pytest.MonkeyPatch,
        seed: SqliteApplicationRepository,
    ) -> None:
        """读完之后、写之前被别处改过 → 409，并且**不把别人的改动盖掉**。

        用**可推进的时钟**自建一个 app：更新带上了读到的版本号（`updated_at`），而
        夹具里的时钟是常量，两次写会同版本 —— 那样校验恒过，测不出任何东西。
        """
        import hunter1.slices.applications.router as router_module

        seed.upsert(_application(id="a1", stage=ApplicationStage.APPLIED))

        moment = [NOW]
        app = FastAPI()
        app.include_router(
            build_router(store=store, jobs=JobStore(db), clock=lambda: moment[0]), prefix="/api"
        )

        original = router_module.service.change_stage

        def racing_change_stage(application, *, stage, now, note=None):  # type: ignore[no-untyped-def]
            mine = original(application, stage=stage, now=now, note=note)
            # 模拟「另一个请求在我们读完之后、写之前先写成功了」
            moment[0] = NOW + timedelta(minutes=1)
            store.update_existing(
                original(
                    application,
                    stage=ApplicationStage.INTERVIEW,
                    now=moment[0],
                    note="别处改的",
                )
            )
            return mine

        monkeypatch.setattr(router_module.service, "change_stage", racing_change_stage)

        with TestClient(app) as racing_client:
            response = racing_client.post(
                "/api/applications/a1/stage", json={"stage": "written_test"}
            )

        assert response.status_code == 409
        assert "请刷新" in response.json()["detail"]
        loaded = store.get("a1")
        assert loaded is not None
        assert loaded.stage is ApplicationStage.INTERVIEW, "别人的改动不许被静默盖掉"
        assert loaded.note == "别处改的"

    def test_overlong_note_is_422(
        self, client: TestClient, store: ApplicationStore, seed: SqliteApplicationRepository
    ) -> None:
        """备注有上限 —— 它是用户手写的自由文本，落进没有列宽约束的 `Text` 列。"""
        seed.upsert(_application(id="a1"))
        response = client.post(
            "/api/applications/a1/stage",
            json={"stage": "interview", "note": "备" * (MAX_NOTE_CHARS + 1)},
        )
        assert response.status_code == 422


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

    def test_repeat_apply_is_not_reported_as_created(
        self, client: TestClient, db: Database
    ) -> None:
        """没有新建资源就不该回 201 —— 201 是「这次真的记了一条」的承诺。"""
        _seed_job(db)
        first = client.post("/api/applications", json={"job_id": JOB_FULL})
        second = client.post("/api/applications", json={"job_id": JOB_FULL})
        assert first.status_code == 201
        assert second.status_code == 200

    def test_concurrent_apply_creates_exactly_one_record(
        self, client: TestClient, db: Database, store: ApplicationStore
    ) -> None:
        """并发下「同一岗位只有一条投递」仍然成立。

        路由是同步 `def`，FastAPI 放线程池**真并行** —— 两个请求都可能在对方落库前
        读到空。这条用例用线程 + 栅栏把那个窗口撑开：修复前会插入两条（断言红），
        修复后由 `uq_applications_job` 拒掉第二个。
        """
        import threading

        _seed_job(db)
        barrier = threading.Barrier(2, timeout=10)
        statuses: list[int] = []
        lock = threading.Lock()

        def apply() -> None:
            barrier.wait()  # 让两个请求尽可能同时进入
            response = client.post("/api/applications", json={"job_id": JOB_FULL})
            with lock:
                statuses.append(response.status_code)

        threads = [threading.Thread(target=apply) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert sorted(statuses) == [200, 201], f"两个请求的状态码：{statuses}"
        assert len(store.by_job(JOB_FULL)) == 1


class TestDeleteEndpoint:
    def test_delete_returns_204_and_removes(
        self,
        client: TestClient,
        store: ApplicationStore,
        seed: SqliteApplicationRepository,
    ) -> None:
        seed.upsert(_application(id="a1"))
        response = client.delete("/api/applications/a1")
        assert response.status_code == 204
        assert store.get("a1") is None
        assert store.count() == 0

    def test_delete_missing_is_204(self, client: TestClient) -> None:
        assert client.delete("/api/applications/zzzz").status_code == 204


class TestApplicationIsASnapshotNotAChildOfJob:
    """`job_id` 是弱引用，**刻意不是外键** —— 这两条测试守住这条设计。

    投递记录是「我申请了什么」的历史事实，`company` / `title` 在投递那一刻从岗位
    复制下来。若有人「顺手补上」`ForeignKey(..., ondelete="CASCADE")`，删掉一个
    岗位就会连带删掉投递历史 —— 那是数据丢失，不是完整性修复。

    一条注释拦不住这种改动（而且看起来像「补齐约束」的善举），所以从**结构**和
    **行为**两侧各钉一颗钉子。
    """

    def test_applications_table_has_no_foreign_keys(self, db: Database) -> None:
        with db.engine.connect() as connection:
            create_sql = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'")
            ).scalar_one()
        assert "FOREIGN KEY" not in create_sql.upper(), (
            "applications 不该有外键：投递记录是岗位信息的快照，加 CASCADE 会在"
            f"删岗位时连带删掉投递历史。实际建表语句：\n{create_sql}"
        )

    def test_snapshot_survives_the_job_row_disappearing(
        self, client: TestClient, db: Database
    ) -> None:
        """岗位行从库里消失后，投递记录仍能读出来，且快照字段完好。

        直接删行（而不是走 API）是**故意**的：现在没有「删岗位」接口，但库里的行
        可能被任何方式移除；这条测试问的是「投递记录是否依赖岗位行还在」。
        """
        _seed_job(db)
        application_id = client.post("/api/applications", json={"job_id": JOB_FULL}).json()[
            "application_id"
        ]

        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM jobs WHERE id = :job_id"), {"job_id": JOB_FULL})
        assert db.jobs().get(JOB_FULL) is None, "前置条件：岗位行确实没了"

        # 没有 GET-by-id 路由，走公开的 list 面读回（这本来也是 router 测试）。
        response = client.get("/api/applications")
        assert response.status_code == 200
        items = [item for item in response.json()["items"] if item["id"] == application_id]
        assert len(items) == 1, "投递记录应仍在"
        assert items[0]["company"] == "字节跳动"
        assert items[0]["title"] == "AI产品经理"
        assert items[0]["job_id"] == JOB_FULL
