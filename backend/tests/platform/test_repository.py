"""仓储层单元测试 —— 临时 SQLite 文件，验证持久化往返、幂等与不变量。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hunter1.domain.models import CaptureStatus, Company, Job
from hunter1.platform.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "hunter1.db")
    database.initialize()
    return database


def _job(**overrides: object) -> Job:
    base: dict[str, object] = {
        "id": "j1",
        "company_id": "c1",
        "title": "AI产品经理（2027校招）",
        "detail_url": "https://example.com/job/1",
        "source": "offerbiu",
    }
    base.update(overrides)
    return Job(**base)


class TestCompanies:
    def test_upsert_then_get_roundtrip(self, db: Database) -> None:
        repo = db.companies()
        company = Company(id="c1", name="字节跳动", source="offerbiu", aliases=["ByteDance"])
        repo.upsert(company)
        assert repo.get("c1") == company

    def test_upsert_updates_in_place(self, db: Database) -> None:
        repo = db.companies()
        repo.upsert(Company(id="c1", name="旧名", source="offerbiu"))
        repo.upsert(Company(id="c1", name="新名", source="offerbiu"))
        assert repo.get("c1") is not None
        assert repo.get("c1").name == "新名"  # type: ignore[union-attr]
        assert repo.count() == 1

    def test_get_missing_returns_none(self, db: Database) -> None:
        assert db.companies().get("missing") is None


class TestJobs:
    def test_roundtrip_preserves_all_fields(self, db: Database) -> None:
        repo = db.jobs()
        job = _job(
            match_score=88,
            capture_status=CaptureStatus.COMPLETE,
            city="北京",
            company_name="字节跳动",
            jd_raw="岗位描述正文",
            first_seen_at=datetime(2026, 9, 1, tzinfo=UTC),
            last_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        repo.upsert(job)
        loaded = repo.get("j1")
        assert loaded == job
        # 归一化投影在往返后仍一致（保证同题折叠稳定）
        assert loaded is not None and loaded.title_key == "ai产品经理"

    def test_upsert_is_idempotent(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job())
        repo.upsert(_job())
        assert repo.count() == 1

    def test_upsert_updates_existing_row(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(match_score=10))
        repo.upsert(_job(match_score=90))
        loaded = repo.get("j1")
        assert loaded is not None and loaded.match_score == 90
        assert repo.count() == 1

    def test_count_by_company(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="j1", company_id="c1"))
        repo.upsert(_job(id="j2", company_id="c1"))
        repo.upsert(_job(id="j3", company_id="c2"))
        assert repo.count() == 3
        assert repo.count(company_id="c1") == 2

    def test_count_by_keyword_matches_search(self, db: Database) -> None:
        """分页要显示「共几页」，所以计数必须和搜索命中同一批记录。"""
        repo = db.jobs()
        repo.upsert(_job(id="j1", title="AI产品经理"))
        repo.upsert(_job(id="j2", title="数据产品经理（2027校招）"))
        repo.upsert(_job(id="j3", title="行政专员"))
        assert repo.count(keyword="产品经理") == 2
        assert repo.count(keyword="产品经理") == len(repo.search(keyword="产品经理", limit=100))

    def test_count_by_keyword_no_match(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="j1", title="AI产品经理"))
        assert repo.count(keyword="律师") == 0

    def test_count_with_empty_keyword_is_total(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="j1", title="AI产品经理"))
        assert repo.count(keyword="") == 1

    def test_list_orders_by_last_seen_desc_nulls_last(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="old", last_seen_at=datetime(2026, 9, 1, tzinfo=UTC)))
        repo.upsert(_job(id="new", last_seen_at=datetime(2026, 10, 1, tzinfo=UTC)))
        repo.upsert(_job(id="never"))  # last_seen_at = None
        assert [j.id for j in repo.list(limit=10)] == ["new", "old", "never"]

    def test_list_pagination(self, db: Database) -> None:
        repo = db.jobs()
        for index in range(5):
            repo.upsert(_job(id=f"j{index}", last_seen_at=datetime(2026, 9, 1 + index, tzinfo=UTC)))
        assert [j.id for j in repo.list(limit=2, offset=0)] == ["j4", "j3"]
        assert [j.id for j in repo.list(limit=2, offset=2)] == ["j2", "j1"]

    def test_list_is_stable_when_last_seen_at_ties(self, db: Database) -> None:
        """同 last_seen_at 时的顺序是**契约**，不是「SQLite 碰巧怎么返回」。

        批量抓取写入的岗位 last_seen_at 全部相同，而 SQL 规范对 ORDER BY 同值行的
        顺序**不作保证**：只依赖实现的偶然，换 SQLite 版本 / 加 ANALYZE / 改索引
        之后顺序就可能变，offset 分页随之跨页重复或丢行。钉住第二键即消除该脆弱性。
        """
        repo = db.jobs()
        same = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        for index in range(5):
            repo.upsert(_job(id=f"j{index}", last_seen_at=same))

        assert [j.id for j in repo.list(limit=10)] == ["j0", "j1", "j2", "j3", "j4"]

        # 翻页拼起来必须恰好是全集：不重复、不缺失
        paged = [j.id for j in repo.list(limit=2, offset=0)]
        paged += [j.id for j in repo.list(limit=2, offset=2)]
        paged += [j.id for j in repo.list(limit=2, offset=4)]
        assert paged == ["j0", "j1", "j2", "j3", "j4"]

    def test_search_order_matches_list_when_last_seen_at_ties(self, db: Database) -> None:
        """`list` 与 `search` 必须给出一致的顺序 —— 否则同一批数据的两种取法互相矛盾。"""
        repo = db.jobs()
        same = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        for index in range(5):
            repo.upsert(_job(id=f"j{index}", title="产品经理", last_seen_at=same))

        assert [j.id for j in repo.search(keyword="产品经理", limit=10)] == [
            j.id for j in repo.list(limit=10)
        ]

    def test_get_by_prefix_is_deterministic_when_last_seen_at_ties(self, db: Database) -> None:
        repo = db.jobs()
        same = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        for index in range(3):
            repo.upsert(_job(id=f"b{index}", last_seen_at=same))

        assert [j.id for j in repo.get_by_prefix("b")] == ["b0", "b1", "b2"]

    def test_get_by_prefix_respects_the_limit(self, db: Database) -> None:
        """前缀查找必须带 LIMIT：单字符前缀在大库上会命中整表。

        调用方只用它「找到唯一那条 / 判断有歧义」，全量取回 + 全量实例化纯属浪费
        （模型给一个 `j` 就要把整个岗位库反序列化一遍）。
        """
        repo = db.jobs()
        same = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        for index in range(5):
            repo.upsert(_job(id=f"b{index}", last_seen_at=same))

        assert len(repo.get_by_prefix("b", limit=2)) == 2
        # 顺序仍由 _JOB_ORDER 决定（取前 N 条也要稳定，否则歧义提示会飘）
        assert [j.id for j in repo.get_by_prefix("b", limit=2)] == ["b0", "b1"]
        assert len(repo.get_by_prefix("b")) == 5, "默认上限不影响小库"

    def test_data_survives_reopen(self, tmp_path: Path) -> None:
        """持久化必须跨连接存续（不是内存态）。"""
        path = tmp_path / "persist.db"
        first = Database(path)
        first.initialize()
        first.jobs().upsert(_job())
        assert first.jobs().count() == 1

        reopened = Database(path)
        reopened.initialize()
        assert reopened.jobs().count() == 1
        assert reopened.jobs().get("j1") is not None


class TestJobSearch:
    """按关键词搜索岗位 —— 助手的 search_jobs 工具依赖它。"""

    def _seed(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(id="j1", title="AI产品经理", company_id="c1"))
        repo.upsert(_job(id="j2", title="大模型产品经理（2027校招）", company_id="c1"))
        repo.upsert(_job(id="j3", title="行政专员", company_id="c2"))
        repo.upsert(_job(id="j4", title="数据产品经理", company_id="c3"))

    def test_matches_normalized_title(self, db: Database) -> None:
        """搜「AI产品经理」应命中归一化后相同的项（含括号补充说明的那条）。"""
        self._seed(db)
        hits = db.jobs().search(keyword="产品经理")
        assert {j.id for j in hits} == {"j1", "j2", "j4"}

    def test_is_case_insensitive_and_fullwidth_tolerant(self, db: Database) -> None:
        self._seed(db)
        assert {j.id for j in db.jobs().search(keyword="ai产品")} == {"j1"}

    def test_no_match_returns_empty(self, db: Database) -> None:
        self._seed(db)
        assert db.jobs().search(keyword="不存在的岗位") == []

    def test_empty_keyword_returns_recent(self, db: Database) -> None:
        """空关键词 = 列出最近岗位（助手「看看有什么」的用法）。"""
        self._seed(db)
        hits = db.jobs().search(keyword="", limit=2)
        assert len(hits) == 2

    def test_respects_limit(self, db: Database) -> None:
        self._seed(db)
        assert len(db.jobs().search(keyword="产品经理", limit=2)) == 2

    def test_wildcard_chars_are_matched_literally(self, db: Database) -> None:
        """`%` / `_` 是 LIKE 通配符 —— 必须转义成字面匹配，否则搜索命中意外行。

        标题归一化不剥离标点，所以岗位标题里可能有这些字符；不转义时搜「100%」
        会把「100A远程」也捞出来（`%` 匹配任意串）。
        """
        repo = db.jobs()
        repo.upsert(_job(id="j1", title="100%远程"))
        repo.upsert(_job(id="j2", title="100A远程"))
        repo.upsert(_job(id="j3", title="a_b工程师"))
        repo.upsert(_job(id="j4", title="axb工程师"))

        assert {j.id for j in repo.search(keyword="100%")} == {"j1"}
        assert repo.count(keyword="100%") == 1
        assert {j.id for j in repo.search(keyword="a_b")} == {"j3"}
        assert repo.count(keyword="a_b") == 1

    def test_orders_by_last_seen_desc(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(
            _job(id="old", title="产品经理A", last_seen_at=datetime(2026, 9, 1, tzinfo=UTC))
        )
        repo.upsert(
            _job(id="new", title="产品经理B", last_seen_at=datetime(2026, 10, 1, tzinfo=UTC))
        )
        assert [j.id for j in repo.search(keyword="产品经理")] == ["new", "old"]


class TestJobScoreIsolation:
    """评分（scoring）与抓取（crawl）各写各的列，读-改-写不得互相覆盖。

    真实竞态：crawl 线程读到旧快照（match_score=None）→ scoring 写入 80 →
    crawl 把整行旧快照写回，把 80 覆盖成 None（分数被静默回滚）。修法是让抓取
    路径只写它拥有的事实列，评分路径只写 match_score 一列。
    """

    def test_upsert_facts_preserves_existing_score(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(match_score=80))
        # crawl 在评分之前读到的快照（其 match_score 还是 None），回写时不得清掉 80
        repo.upsert_facts(_job(match_score=None, title="改名后的标题"))
        loaded = repo.get("j1")
        assert loaded is not None
        assert loaded.match_score == 80
        assert loaded.title == "改名后的标题"  # 事实列照常更新

    def test_upsert_facts_updates_other_columns(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(title="旧标题", city=None, match_score=40))
        repo.upsert_facts(_job(title="新标题", city="上海", match_score=None))
        loaded = repo.get("j1")
        assert loaded is not None
        assert loaded.title == "新标题"
        assert loaded.city == "上海"
        assert loaded.match_score == 40

    def test_upsert_facts_inserts_when_absent(self, db: Database) -> None:
        """抓取到新岗位时走同一函数 —— 新行照常插入。"""
        db.jobs().upsert_facts(_job(id="fresh"))
        assert db.jobs().get("fresh") is not None

    def test_set_match_score_is_targeted(self, db: Database) -> None:
        repo = db.jobs()
        repo.upsert(_job(title="原标题", city="北京"))
        assert repo.set_match_score("j1", 88) is True
        loaded = repo.get("j1")
        assert loaded is not None
        assert loaded.match_score == 88
        assert loaded.title == "原标题"  # 其它列不被整行覆盖
        assert loaded.city == "北京"

    def test_set_match_score_missing_job_returns_false(self, db: Database) -> None:
        assert db.jobs().set_match_score("ghost", 50) is False


class TestIllegalEnumValuesInDatabase:
    """库里的非法枚举值不能把整张列表拖成 500。

    行映射直接构造枚举会抛 `ValueError`，而它发生在**读列表**的路径上：一行坏数据
    让整个岗位库页面 500，用户既看不到是哪一行，也没有修库的入口。非法值只可能
    来自手工改库或更早的版本。

    约定与 `Database.initialize` 的重复序号修复一致：容忍/改动了用户数据必须留痕
    （stderr 一行），不能静默。
    """

    def _corrupt(self, db: Database, *, column: str, value: str) -> None:
        import sqlalchemy as sa

        with db.engine.begin() as conn:
            conn.execute(
                sa.text(f"UPDATE jobs SET {column} = :v WHERE id = :i"),
                {"v": value, "i": "j1"},
            )

    def test_illegal_capture_status_falls_back_with_a_warning(
        self, db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db.jobs().upsert(_job())
        self._corrupt(db, column="capture_status", value="bogus")

        jobs = db.jobs().list()  # 不应抛

        assert len(jobs) == 1
        assert jobs[0].capture_status is CaptureStatus.UNKNOWN
        assert "bogus" in capsys.readouterr().err

    def test_corrupt_row_does_not_hide_its_healthy_neighbours(
        self, db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """一行坏数据不该让其它行一起消失（整张列表 500 的后果就是这个）。"""
        db.jobs().upsert(_job(id="j1", title="坏行"))
        db.jobs().upsert(_job(id="j2", title="好行"))
        self._corrupt(db, column="capture_status", value="not-a-status")

        titles = {job.title for job in db.jobs().list()}

        assert titles == {"好行", "坏行"}
        capsys.readouterr()


def test_negative_limit_is_rejected(db: Database) -> None:
    """SQLite 的 `LIMIT -1` 是**不限量**。

    调用方漏了校验时，「取一页」会静默变成「取整库」—— 方向正好相反，而且毫无迹象。
    """
    with pytest.raises(ValueError):
        db.jobs().list(limit=-1)
    with pytest.raises(ValueError):
        db.jobs().list(offset=-1)
    with pytest.raises(ValueError):
        db.jobs().search(keyword="产品", limit=-1)
