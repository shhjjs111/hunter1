"""投递记录仓储测试 —— 临时 SQLite，验证往返、排序与幂等。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
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

    def test_by_job_prefix_finds_the_id_the_assistant_shows(self, db: Database) -> None:
        """助手展示的是 8 位前缀 —— 前缀查询要认得它（否则「你没投过」是假否定）。"""
        repo = db.applications()
        long_id = "abcdef" + "0" * 26
        repo.upsert(_application(job_id=long_id))

        assert repo.by_job(long_id[:8]) == [], "精确查询不该前缀匹配（语义要分开）"
        found = repo.by_job_prefix(long_id[:8])
        assert [a.job_id for a in found] == [long_id]

    def test_by_job_prefix_limits_and_escapes(self, db: Database) -> None:
        """单字符前缀会命中很多行 → 必须带 LIMIT；LIKE 通配符按字面匹配。"""
        repo = db.applications()
        for index in range(5):
            repo.upsert(_application(id=f"a{index}", job_id=f"job-{index}"))

        assert len(repo.by_job_prefix("job-", limit=2)) == 2
        assert repo.by_job_prefix("") == [], "空前缀不是「全表」"
        # `%` 是 LIKE 通配符：不转义的话 "job%" 会匹到全部 5 条
        assert repo.by_job_prefix("%") == []

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
        # 每行必须挂**不同**岗位：同一岗位至多一条投递，由 uq_applications_job 兜底
        repo.upsert(
            _application(id="old", job_id="j-old", updated_at=datetime(2026, 9, 2, tzinfo=UTC))
        )
        repo.upsert(
            _application(id="new", job_id="j-new", updated_at=datetime(2026, 10, 2, tzinfo=UTC))
        )
        assert [a.id for a in repo.list()] == ["new", "old"]

    def test_list_pagination(self, db: Database) -> None:
        repo = db.applications()
        for index in range(5):
            repo.upsert(
                _application(
                    id=f"a{index}",
                    job_id=f"j{index}",
                    updated_at=datetime(2026, 9, 1 + index, tzinfo=UTC),
                )
            )
        assert [a.id for a in repo.list(limit=2)] == ["a4", "a3"]
        assert [a.id for a in repo.list(limit=2, offset=2)] == ["a2", "a1"]

    def test_list_is_stable_when_updated_at_ties(self, db: Database) -> None:
        """同 updated_at 时的顺序是契约：分页基于 offset，序不确定就会跨页重复/丢行。"""
        repo = db.applications()
        same = datetime(2026, 9, 2, tzinfo=UTC)
        for index in range(4):
            repo.upsert(_application(id=f"a{index}", job_id=f"j{index}", updated_at=same))
        assert [a.id for a in repo.list()] == ["a0", "a1", "a2", "a3"]

    def test_insert_for_job_rejects_a_second_row_for_the_same_job(self, db: Database) -> None:
        """同一岗位的第二条投递被**数据库**拒绝，并返回既有那条。

        这是并发兜底：service 的先读后写在两个请求都读到空时失效，唯一索引是最后
        一道闸。返回既有记录让调用方看到幂等语义（而不是 IntegrityError）。
        """
        repo = db.applications()
        first = repo.insert_for_job(_application(id="a1", job_id="j1"))
        second = repo.insert_for_job(_application(id="a2", job_id="j1"))
        assert first.id == "a1"
        assert second.id == "a1", "撞唯一约束时应返回既有那条"
        assert repo.count() == 1

    def test_insert_for_job_allows_other_jobs(self, db: Database) -> None:
        repo = db.applications()
        repo.insert_for_job(_application(id="a1", job_id="j1"))
        repo.insert_for_job(_application(id="a2", job_id="j2"))
        assert repo.count() == 2

    def test_update_existing_does_not_insert_missing_row(self, db: Database) -> None:
        """已被删的记录不得被「更新」步骤插回去（静默撤销删除）。"""
        repo = db.applications()
        assert repo.update_existing(_application(id="ghost", job_id="j9")) is False
        assert repo.count() == 0

    def test_update_existing_reports_true_for_a_real_row(self, db: Database) -> None:
        repo = db.applications()
        repo.upsert(_application(id="a1", job_id="j1"))
        assert repo.update_existing(_application(id="a1", job_id="j1")) is True

    def test_unique_index_is_actually_created(self, db: Database) -> None:
        """唯一约束得真的在库里 —— 它是并发下唯一的兜底，别只在文档里存在。"""
        from sqlalchemy import text

        with db.engine.connect() as connection:
            indexes = connection.execute(text("PRAGMA index_list('applications')")).mappings().all()
        unique = {row["name"] for row in indexes if row["unique"]}
        assert "uq_applications_job" in unique, f"缺唯一索引，现有：{sorted(unique)}"

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


class TestIllegalStageInDatabase:
    """同理于岗位状态：库里的非法阶段值不能让整个投递列表 500。

    非法值来自手工改库或更早的版本；退回到模型声明的默认阶段（`APPLIED`）
    并在 stderr 留一行 —— 容忍用户数据就不能静默。
    """

    def test_illegal_stage_falls_back_with_a_warning(
        self, db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import sqlalchemy as sa

        db.applications().upsert(_application(stage=ApplicationStage.OFFER))
        with db.engine.begin() as conn:
            conn.execute(sa.text("UPDATE applications SET stage = 'bogus' WHERE id = 'a1'"))

        applications = db.applications().list()  # 不应抛

        assert len(applications) == 1
        assert applications[0].stage is ApplicationStage.APPLIED
        assert "bogus" in capsys.readouterr().err


class TestRepairOfDuplicateApplications:
    """老库在唯一索引落地前撞出「同岗位多条投递」时，启动自愈而不是把应用挡在门外。

    重复行只可能来自旧版 `apply_to_job` 的先读后写竞态（两个请求都读到空）。
    先收敛再建索引 —— 否则 `CREATE UNIQUE INDEX` 抛裸 IntegrityError，而本方法在
    `AppContext.default` 的启动路径上：应用起不来、用户无从自救。
    """

    def _make_dirty(self, db: Database) -> None:
        import sqlalchemy as sa

        with db.engine.begin() as conn:
            conn.execute(sa.text("DROP INDEX IF EXISTS uq_applications_job"))
            for application_id, updated in (
                ("a-old", "2026-01-01 00:00:00"),
                ("a-new", "2026-06-01 00:00:00"),
            ):
                conn.execute(
                    sa.text(
                        "INSERT INTO applications "
                        "(id,job_id,company,title,stage,applied_at,updated_at,note) "
                        "VALUES (:id,'j1','字节跳动','AI产品经理','applied',"
                        "'2026-01-01 00:00:00',:updated,NULL)"
                    ),
                    {"id": application_id, "updated": updated},
                )

    def test_initialize_heals_instead_of_crashing(
        self, db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._make_dirty(db)
        db.initialize()  # 不应抛裸 IntegrityError

        rows = db.applications().by_job("j1")
        assert len(rows) == 1
        assert rows[0].id == "a-new", "应保留更新的那条"
        # 删用户数据必须留痕
        assert "重复投递" in capsys.readouterr().err

    def test_cleanish_database_reports_zero_repairs(self, db: Database) -> None:
        with db.engine.begin() as conn:
            assert Database._collapse_duplicate_applications(conn) == 0

    def test_dirty_database_reports_repair_count(self, db: Database) -> None:
        self._make_dirty(db)
        with db.engine.begin() as conn:
            assert Database._collapse_duplicate_applications(conn) == 1

    def test_stale_non_unique_index_is_dropped(self, db: Database) -> None:
        """升级后旧的非唯一索引要被清掉 —— 否则同列留下两个索引（冗余）。"""
        import sqlalchemy as sa

        with db.engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_applications_job_id ON applications (job_id)"
                )
            )
        db.initialize()

        with db.session() as session:
            names = set(
                session.scalars(
                    sa.text(
                        "SELECT name FROM sqlite_master WHERE type='index' "
                        "AND tbl_name='applications'"
                    )
                )
            )
        assert "ix_applications_job_id" not in names
        assert "uq_applications_job" in names
