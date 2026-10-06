"""投递记录仓储测试 —— 临时 SQLite，验证往返、排序与幂等。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter1.domain.models import Application, ApplicationStage
from hunter1.platform.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "applications.db")
    database.initialize()
    return database


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


class TestApplicationRepository:
    def test_roundtrip_preserves_all_fields(self, db: Database) -> None:
        repo = db.applications()
        application = _application(stage=ApplicationStage.INTERVIEW, note="二面在周三")
        repo.upsert(application)
        assert repo.get("a1") == application

    def test_get_missing_returns_none(self, db: Database) -> None:
        assert db.applications().get("missing") is None

    def test_upsert_is_idempotent(self, db: Database) -> None:
        repo = db.applications()
        repo.upsert(_application())
        repo.upsert(_application())
        assert repo.count() == 1

    def test_upsert_updates_stage_in_place(self, db: Database) -> None:
        repo = db.applications()
        repo.upsert(_application())
        repo.upsert(
            _application(
                stage=ApplicationStage.OFFER,
                updated_at=datetime(2026, 9, 10, tzinfo=UTC),
            )
        )
        loaded = repo.get("a1")
        assert loaded is not None
        assert loaded.stage is ApplicationStage.OFFER
        assert repo.count() == 1

    def test_list_orders_by_updated_at_desc(self, db: Database) -> None:
        repo = db.applications()
        repo.upsert(_application(id="old", updated_at=datetime(2026, 9, 2, tzinfo=UTC)))
        repo.upsert(_application(id="new", updated_at=datetime(2026, 10, 2, tzinfo=UTC)))
        assert [a.id for a in repo.list()] == ["new", "old"]

    def test_list_pagination(self, db: Database) -> None:
        repo = db.applications()
        for index in range(5):
            repo.upsert(
                _application(id=f"a{index}", updated_at=datetime(2026, 9, 1 + index, tzinfo=UTC))
            )
        assert [a.id for a in repo.list(limit=2)] == ["a4", "a3"]
        assert [a.id for a in repo.list(limit=2, offset=2)] == ["a2", "a1"]

    def test_by_job_returns_matches(self, db: Database) -> None:
        repo = db.applications()
        repo.upsert(_application(id="a1", job_id="j1"))
        repo.upsert(_application(id="a2", job_id="j2"))
        assert [a.id for a in repo.by_job("j1")] == ["a1"]
        assert repo.by_job("nope") == []

    def test_delete_removes_row(self, db: Database) -> None:
        repo = db.applications()
        repo.upsert(_application())
        repo.delete("a1")
        assert repo.get("a1") is None
        assert repo.count() == 0

    def test_delete_missing_is_noop(self, db: Database) -> None:
        db.applications().delete("missing")

    def test_data_survives_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "persist.db"
        first = Database(path)
        first.initialize()
        first.applications().upsert(_application())
        first.dispose()

        reopened = Database(path)
        reopened.initialize()
        assert reopened.applications().count() == 1
