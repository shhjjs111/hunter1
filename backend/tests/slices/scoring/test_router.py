"""scoring 切片 HTTP 面测试 —— 真 SQLite + TestClient + 假 LLM，全程离线。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hunter1.domain.models import CaptureStatus, Job
from hunter1.platform.db import Database
from hunter1.platform.llm import LLMError, LLMResponse
from hunter1.slices.scoring.models import CandidateProfile
from hunter1.slices.scoring.prompts import PROMPT_VERSION
from hunter1.slices.scoring.router import build_router
from hunter1.slices.scoring.store import ScoreStore

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
JOB_ID = "s" * 32

PROFILE = CandidateProfile(keywords=["AI产品经理"], summary="测试画像")


class FakeLLM:
    def __init__(self, payload: dict[str, Any] | None = None, *, boom: bool = False) -> None:
        self.payload = payload if payload is not None else {"score": 77}
        self.boom = boom
        self.closed = False

    def close(self) -> None:
        """端口要求：用完释放（真实客户端会关掉 httpx 连接池）。"""
        self.closed = True

    def complete_structured(self, **_kwargs: Any) -> LLMResponse:
        if self.boom:
            # 端口契约：LLMProvider 失败时抛 LLMError（见 application/ports.py）
            raise LLMError("upstream_failed", "model endpoint unreachable")
        return LLMResponse(content=json.dumps(self.payload), model="fake")


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "scoring.db")
    database.initialize()
    database.jobs().upsert(
        Job(
            id=JOB_ID,
            company_id="c1",
            title="AI产品经理",
            detail_url="https://x/1",
            source="实习僧",
            company_name="字节跳动",
            capture_status=CaptureStatus.COMPLETE,
            jd_raw="负责大模型产品。",
            last_seen_at=NOW,
        )
    )
    return database


def _client(
    db: Database, llm: FakeLLM, *, profile: CandidateProfile | None = PROFILE
) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(
        build_router(
            store=ScoreStore(db),
            llm_factory=lambda: llm,
            profile_provider=lambda: profile,
        ),
        prefix="/api",
    )
    with TestClient(app) as test_client:
        yield test_client


class TestScoreEndpoint:
    def test_scores_and_persists(self, db: Database) -> None:
        for client in _client(db, FakeLLM({"score": 88, "summary": "可投"})):
            response = client.post(f"/api/scoring/{JOB_ID}")
            assert response.status_code == 200
            payload = response.json()
            assert payload["job_id"] == JOB_ID
            assert payload["score"] == 88
            assert payload["summary"] == "可投"

            # 关键：分数**真的写回了库**（不是只在响应里）
            reloaded = db.jobs().get(JOB_ID)
            assert reloaded is not None
            assert reloaded.match_score == 88

    def test_provenance_is_persisted_not_only_returned(self, db: Database) -> None:
        """模型与提示词版本要落库 —— `prompts.py` 说 PROMPT_VERSION 就是为此存在的。

        原先它们只出现在 HTTP 响应里：刷新页面就再也查不到「这条分是哪版打的」，
        而那正是评分辨识度出问题时唯一需要的线索。
        """
        import sqlalchemy as sa

        for client in _client(db, FakeLLM({"score": 77})):
            assert client.post(f"/api/scoring/{JOB_ID}").status_code == 200

        with db.session() as session:
            row = session.execute(
                sa.text(
                    "SELECT score_model, score_prompt_version, scored_at FROM jobs WHERE id = :job"
                ),
                {"job": JOB_ID},
            ).one()
        assert row.score_model == "fake", "模型名取自 LLM 响应"
        assert row.score_prompt_version == PROMPT_VERSION
        assert row.scored_at is not None, "还缺打分时间 —— 回溯需要它"

    def test_conclusion_texts_are_persisted_and_readable_back(self, db: Database) -> None:
        """优势 / 差距 / 摘要要落库，并且**读得回来**。

        与上面那条同因：原先 `summary` 只在响应里闪一下、`advantages` / `gaps` 连响应
        都没进 —— 模型算出来的东西刷新页面即无据可查。落库之外还得有读回去的路
        （`GET`），否则「落库」只是自欺。
        """
        payload = {
            "score": 77,
            "advantages": "有 LLM 落地经验",
            "gaps": "缺大规模团队经验",
            "summary": "总体匹配",
        }
        for client in _client(db, FakeLLM(payload)):
            response = client.post(f"/api/scoring/{JOB_ID}")
            assert response.status_code == 200
            body = response.json()
            assert body["advantages"] == "有 LLM 落地经验"
            assert body["gaps"] == "缺大规模团队经验"
            assert body["summary"] == "总体匹配"

            stored = client.get(f"/api/scoring/{JOB_ID}")
            assert stored.status_code == 200
            assert stored.json() == body, "POST 与 GET 必须给同一份结论（前端共用一套渲染）"

    def test_never_scored_job_is_404_on_read(self, db: Database) -> None:
        """还没评过 → 404 + 可读原因。

        不是返回 `null`：本端点的语义是「读一份**已存在**的评分」，没有就是不满足
        前置条件。与 `GET /scoring/profile` 的取舍刻意不同 —— 那边 `null` 是正常初始态。
        """
        for client in _client(db, FakeLLM()):
            response = client.get(f"/api/scoring/{JOB_ID}")
            assert response.status_code == 404
            assert "还没有评过" in response.json()["detail"]

            missing = client.get("/api/scoring/ghost")
            assert missing.status_code == 404
            assert "岗位不存在" in missing.json()["detail"]

    def test_missing_job_is_404(self, db: Database) -> None:
        for client in _client(db, FakeLLM()):
            assert client.post("/api/scoring/zzzz").status_code == 404

    def test_job_vanishing_between_load_and_save_is_404(self, db: Database) -> None:
        """L8：load 成功、写分前岗位被删 —— save_score 返回 None 时路由必须报 404。

        修复前路由忽略返回值，会返回 200 声称已写分（而库里什么都没写）。
        """

        class VanishingStore(ScoreStore):
            def save_score(self, job_id: str, score: int, **overrides: object):  # type: ignore[override]
                return None  # 岗位在 load 与 save 之间被删

        app = FastAPI()
        app.include_router(
            build_router(
                store=VanishingStore(db),
                llm_factory=lambda: FakeLLM({"score": 88}),
                profile_provider=lambda: PROFILE,
            ),
            prefix="/api",
        )
        with TestClient(app) as client:
            response = client.post(f"/api/scoring/{JOB_ID}")
        assert response.status_code == 404
        assert "岗位不存在" in response.json()["detail"]

    def test_llm_failure_is_422_with_reason(self, db: Database) -> None:
        """模型侧失败 → 422（不是 500，也不是 404）：请求没毛病，是依赖给不出结果。"""
        for client in _client(db, FakeLLM(boom=True)):
            response = client.post(f"/api/scoring/{JOB_ID}")
            assert response.status_code == 422
            assert "llm failed" in response.json()["detail"]

    def test_bad_model_output_is_422_and_does_not_write(self, db: Database) -> None:
        """模型输出不可用时不得写回分数 —— 「没评上」不能变成「评了 0 分」。"""
        for client in _client(db, FakeLLM({"advantages": "只有优点"})):
            assert client.post(f"/api/scoring/{JOB_ID}").status_code == 422
            reloaded = db.jobs().get(JOB_ID)
            assert reloaded is not None
            assert reloaded.match_score is None

    def test_prompt_version_is_reported(self, db: Database) -> None:
        for client in _client(db, FakeLLM()):
            payload = client.post(f"/api/scoring/{JOB_ID}").json()
            assert payload["prompt_version"] is not None


class TestUnexpectedFailure:
    """非契约异常（实现 bug）不该被当成「模型失败」吞掉。

    `LLMProvider` 的实现约定失败抛 `LLMError`；若客户端抛出别的东西，
    那是**实现 bug**，必须响亮地失败（500），而不是伪装成 422 的「模型不可用」。
    两者的排查路径完全不同。
    """

    def test_non_contract_exception_fails_loud(self, db: Database) -> None:
        class BrokenLLM:
            def complete_structured(self, **_kwargs: Any) -> LLMResponse:
                raise TypeError("实现 bug")

        app = FastAPI()
        app.include_router(
            build_router(
                store=ScoreStore(db),
                llm_factory=BrokenLLM,
                profile_provider=lambda: PROFILE,
            ),
            prefix="/api",
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.post(f"/api/scoring/{JOB_ID}").status_code == 500


class TestProfileEndpoints:
    """候选人画像的读写 —— 用户能自己配置，且改动立刻对评分生效。

    这里**不注入固定画像**，provider 就是 `store.load_profile`（与生产装配同款），
    因此覆盖的是真实持久化路径，而不是测试替身。
    """

    def _client(self, db: Database, llm: FakeLLM) -> TestClient:
        store = ScoreStore(db)
        app = FastAPI()
        app.include_router(
            build_router(
                store=store,
                llm_factory=lambda: llm,
                profile_provider=store.load_profile,
            ),
            prefix="/api",
        )
        return TestClient(app)

    def test_starts_unconfigured_not_404(self, db: Database) -> None:
        """未配置是**初始状态**，用 200 + null 表达；404 会让人以为是路由错了。

        `warning` 必须是空的：它专用来报「已存的画像**坏了**」，而全新安装带上它
        等于告诉用户「你存过的东西坏了」—— 那正是 store 文档要分辨的两个状态
        （实测：把 `load_profile` 的「未配置返回 None」去掉，用例照样绿，
        因为只断言了 profile 是 null）。
        """
        with self._client(db, FakeLLM()) as client:
            response = client.get("/api/scoring/profile")
            assert response.status_code == 200
            assert response.json()["profile"] is None
            assert response.json()["warning"] is None

    def test_save_then_read_back(self, db: Database) -> None:
        with self._client(db, FakeLLM()) as client:
            saved = client.put(
                "/api/scoring/profile",
                json={"keywords": ["AI产品经理"], "directions": ["大模型"], "summary": "三年经验"},
            )
            assert saved.status_code == 200
            assert saved.json()["profile"]["keywords"] == ["AI产品经理"]

            got = client.get("/api/scoring/profile")
            assert got.json()["profile"]["summary"] == "三年经验"

    def test_empty_profile_rejected_actionably(self, db: Database) -> None:
        """三项全空等于没给信号 —— 拒绝并说清缺什么，而不是存一个空画像。"""
        with self._client(db, FakeLLM()) as client:
            response = client.put(
                "/api/scoring/profile",
                json={"keywords": [], "directions": [], "summary": ""},
            )
            assert response.status_code == 422
            assert "画像" in response.json()["detail"]

    def test_score_without_profile_is_409_not_405(self, db: Database) -> None:
        """端点**必须存在**：画像没配时也给 409 + 指引，不能是「端点不存在」。"""
        with self._client(db, FakeLLM()) as client:
            response = client.post(f"/api/scoring/{JOB_ID}")
            assert response.status_code == 409
            assert "画像" in response.json()["detail"]

    def test_score_works_after_profile_saved(self, db: Database) -> None:
        """端到端可达：写画像 → 评分 → 分数落到岗位上。"""
        with self._client(db, FakeLLM({"score": 88})) as client:
            assert client.put("/api/scoring/profile", json={"keywords": ["AI"]}).status_code == 200
            response = client.post(f"/api/scoring/{JOB_ID}")
            assert response.status_code == 200
            assert response.json()["score"] == 88

        reloaded = db.jobs().get(JOB_ID)
        assert reloaded is not None
        assert reloaded.match_score == 88

    def test_oversized_keywords_rejected(self, db: Database) -> None:
        """画像会被原样拼进提示词 —— 无上限时一次评分就是数十万 token。

        实测（修复前）：5000 个关键词 + 10 万字符摘要 → 200 且入库，
        生成的提示词 119,085 字符。这不是「有点大」，是拿用户的额度去撞厂商上限。
        """
        with self._client(db, FakeLLM()) as client:
            response = client.put("/api/scoring/profile", json={"keywords": ["AI"] * 5000})
            assert response.status_code == 422, "超量关键词必须被挡在入库之前"
            assert "上限" in response.json()["detail"]

    def test_oversized_summary_rejected(self, db: Database) -> None:
        with self._client(db, FakeLLM()) as client:
            response = client.put("/api/scoring/profile", json={"summary": "x" * 100_000})
            assert response.status_code == 422

    def test_oversized_single_item_rejected(self, db: Database) -> None:
        """单条关键词也不能无限长 —— 「一条」不等于「很短」。"""
        with self._client(db, FakeLLM()) as client:
            response = client.put("/api/scoring/profile", json={"keywords": ["x" * 10_000]})
            assert response.status_code == 422

    def test_rejection_does_not_overwrite_stored_profile(self, db: Database) -> None:
        """被拒的请求不得把已存画像改坏 —— 校验必须在写入之前。"""
        with self._client(db, FakeLLM()) as client:
            assert (
                client.put("/api/scoring/profile", json={"keywords": ["保留我"]}).status_code == 200
            )
            assert (
                client.put("/api/scoring/profile", json={"keywords": ["x"] * 5000}).status_code
                == 422
            )
            got = client.get("/api/scoring/profile").json()
            assert got["profile"]["keywords"] == ["保留我"]

    def test_broken_stored_profile_is_repairable_not_500(self, db: Database) -> None:
        """存储里的画像不合法时，要能打开配置页去修 —— 不能 500。

        触发场景是真实的：用户按旧规则存过一个超大画像，随后上限生效，那条数据
        就变成「非法」。若 GET 直接 500，用户拿不到表单，**永远修不了**。
        也不能静默当成「没配过」—— 那会掩盖损坏。故：空画像 + 可读原因。
        """
        from hunter1.slices.scoring.store import PROFILE_KEY

        db.settings().set_raw(PROFILE_KEY, {"keywords": ["x" * 10_000]})

        with self._client(db, FakeLLM()) as client:
            response = client.get("/api/scoring/profile")

        assert response.status_code == 200, f"期望可修复（200），实际 {response.status_code}"
        body = response.json()
        assert body["profile"] is None
        assert body["warning"], "必须说明「已存的画像有问题」，不能静默当作没配过"

    def test_scoring_with_broken_stored_profile_is_409_not_500(self, db: Database) -> None:
        """存储里的画像不合法时，评分给 409 + 可行动原因，不是 500。

        与 GET 的处理**刻意不同**：GET 要能打开表单（200 + warning），
        评分要明确拒绝（409）—— 状态未就绪就不该往下走。
        两处若有一处漏了，用户就看到 500 而非「去重填画像」。
        """
        from hunter1.slices.scoring.store import PROFILE_KEY

        db.settings().set_raw(PROFILE_KEY, {"keywords": ["x" * 10_000]})

        with self._client(db, FakeLLM()) as client:
            response = client.post(f"/api/scoring/{JOB_ID}")

        assert response.status_code == 409, (
            f"期望 409，实际 {response.status_code}：{response.text[:120]}"
        )
        assert "画像" in response.json()["detail"]


class TestLlmClientIsReleased:
    """客户端是**每请求新建**的（改配置要立刻生效），所以必须用完即关 —— 否则真实的
    httpx 连接池会随请求数累积。这条守的是「路由真的调了 close」。
    """

    def test_score_releases_client(self, db: Database) -> None:
        llm = FakeLLM({"score": 66, "summary": "还行"})
        for client in _client(db, llm):
            assert client.post(f"/api/scoring/{JOB_ID}").status_code == 200
        assert llm.closed is True, "评分端点必须释放客户端"

    def test_score_releases_client_even_on_failure(self, db: Database) -> None:
        """失败路径也要释放 —— try/finally，不是只在成功分支 close。"""
        llm = FakeLLM(boom=True)
        for client in _client(db, llm):
            assert client.post(f"/api/scoring/{JOB_ID}").status_code == 422
        assert llm.closed is True, "失败也必须释放客户端"
