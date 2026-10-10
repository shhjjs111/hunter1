"""scoring 切片持久化门面测试 —— 真 SQLite，全程离线。

重点锁「分数不会被抓取线程的整行写静默回滚」这条不变量（见 TestScoreIsolation）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hunter1.domain.models import Job
from hunter1.platform.db import Database
from hunter1.slices.scoring.models import CandidateProfile
from hunter1.slices.scoring.store import PROFILE_KEY, ScoreStore

JOB_ID = "b" * 32


def _job(**overrides: object) -> Job:
    base: dict[str, object] = {
        "id": JOB_ID,
        "company_id": "c1",
        "title": "AI产品经理",
        "detail_url": "https://example.com/job/1",
        "source": "offerbiu",
        "company_name": "字节跳动",
        "city": "北京",
    }
    base.update(overrides)
    return Job(**base)


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "scoring-store.db")
    database.initialize()
    return database


@pytest.fixture()
def store(db: Database) -> ScoreStore:
    return ScoreStore(db)


class TestSaveScore:
    def test_persists_score(self, store: ScoreStore, db: Database) -> None:
        db.jobs().upsert(_job())
        updated = store.save_score(JOB_ID, 80)
        assert updated is not None and updated.match_score == 80
        assert db.jobs().get(JOB_ID).match_score == 80  # type: ignore[union-attr]

    def test_missing_job_returns_none(self, store: ScoreStore) -> None:
        assert store.save_score("ghost", 80) is None

    def test_out_of_range_score_is_rejected(self, store: ScoreStore, db: Database) -> None:
        """定向列写绕过了 pydantic 约束，写库前必须显式兜住取值域。"""
        db.jobs().upsert(_job())
        with pytest.raises(ValueError):
            store.save_score(JOB_ID, 200)
        assert db.jobs().get(JOB_ID).match_score is None  # type: ignore[union-attr]

    @pytest.mark.parametrize("bad", [88.9, "88", None])
    def test_non_integer_score_is_rejected_before_writing(
        self, store: ScoreStore, db: Database, bad: object
    ) -> None:
        """非整数分数必须在写库**之前**被拒 —— 否则这个岗位此后读不出来。

        这是「坏值落库、整行读 500」那条缺陷的护栏：`Job.match_score` 是 `int`，而
        写分走定向列、绕过 pydantic。放 88.9 进去之后，`db.jobs().get(JOB_ID)`
        （也就是 `GET /api/jobs`、`GET /api/jobs/{id}`）每一次都抛 ValidationError。
        断言分两层：抛 ValueError（不是 TypeError —— 调用方按 ValueError 接），
        以及库**没被污染**（这一行照旧读得出来）。
        """
        db.jobs().upsert(_job())

        with pytest.raises(ValueError):
            store.save_score(JOB_ID, bad)  # type: ignore[arg-type]

        assert db.jobs().get(JOB_ID).match_score is None  # type: ignore[union-attr]

    def test_does_not_clobber_other_columns(self, store: ScoreStore, db: Database) -> None:
        """只写 match_score 一列 —— 抓取并发更新的标题/城市不被回滚。"""
        db.jobs().upsert(_job(title="原标题"))
        db.jobs().upsert_facts(_job(title="抓取更新的标题"))
        store.save_score(JOB_ID, 90)
        loaded = db.jobs().get(JOB_ID)
        assert loaded is not None
        assert loaded.title == "抓取更新的标题"
        assert loaded.match_score == 90


class TestScoreIsolation:
    def test_score_survives_concurrent_crawl_rewrite(self, store: ScoreStore, db: Database) -> None:
        """复现报告里的交错：crawl 读旧快照 → scoring 写分 → crawl 回写旧快照。

        修复前 crawl 的整行写会把刚打的 80 覆盖回 None；修复后抓取路径只写事实列，
        分数保持 80。
        """
        db.jobs().upsert(_job())
        stale = db.jobs().get(JOB_ID)  # crawl 在评分之前读到的快照（untouched）
        assert stale is not None

        store.save_score(JOB_ID, 80)  # scoring 写分

        # crawl 用它那份旧快照回写（stale.match_score 仍是 None）
        db.jobs().upsert_facts(stale)

        assert db.jobs().get(JOB_ID).match_score == 80  # type: ignore[union-attr]


class TestProfileLoadDistinguishesStates:
    """「还没配过」与「存坏了」必须能分辨 —— 这是本模块文档给的承诺。

    这条分支**已经被钉住**：把 `load_profile` 里 `if raw is None: return None` 整段
    去掉（未配置时直接 `CandidateProfile.model_validate(None)` → ValidationError →
    抛「画像不合法」），本类与路由侧的用例会红（本轮实测 2 failed）—— 「全新安装的
    用户第一次打开配置页看到『已保存的画像不可用』」这种回归有人守。

    （原注释称「全部 scoring 用例照样绿」，那是更早一轮的实测结论；用例集补齐之后
    它不再成立，故照实改掉 —— 留着一句已经为假的实测记录，比没有记录更坏。）
    """

    def test_never_configured_is_none_not_an_error(self, store: ScoreStore) -> None:
        assert store.load_profile() is None

    def test_saved_profile_round_trips(self, store: ScoreStore) -> None:
        store.save_profile(CandidateProfile(keywords=["AI产品经理"], summary="三年经验"))
        loaded = store.load_profile()
        assert loaded is not None
        assert loaded.keywords == ["AI产品经理"] and loaded.summary == "三年经验"

    def test_corrupted_profile_raises_with_a_readable_reason(
        self, store: ScoreStore, db: Database
    ) -> None:
        """存坏了要说**不可用**（而不是静默当作没配过 —— 那会掩盖损坏）。"""
        db.settings().set_raw(PROFILE_KEY, {"keywords": "不是列表"})
        with pytest.raises(ValueError) as excinfo:
            store.load_profile()
        assert "画像" in str(excinfo.value)


def test_overlong_conclusion_text_is_clipped_with_a_trace(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """外部模型给的文本长度不受控 —— 截断，但**不静默**。

    一段跑飞的长文会按行复制进库（岗位行数没有上限），所以要截；而静默截断会让人
    以为模型就写了这么点，所以要留痕。
    """
    from hunter1.slices.scoring.store import MAX_SCORE_TEXT_CHARS

    db.jobs().upsert(_job())
    stored = ScoreStore(db).save_score(JOB_ID, 70, summary="观" * (MAX_SCORE_TEXT_CHARS + 50))

    assert stored is not None
    assert len(stored.score_summary or "") == MAX_SCORE_TEXT_CHARS
    assert "已截断" in capsys.readouterr().err
