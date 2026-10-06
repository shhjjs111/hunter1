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
from hunter1.slices.scoring.router import build_router
from hunter1.slices.scoring.store import ScoreStore

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
JOB_ID = "s" * 32

PROFILE = CandidateProfile(keywords=["AI产品经理"], summary="测试画像")


class FakeLLM:
    def __init__(self, payload: dict[str, Any] | None = None, *, boom: bool = False) -> None:
        self.payload = payload if payload is not None else {"score": 77}
        self.boom = boom

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

    def test_missing_job_is_404(self, db: Database) -> None:
        for client in _client(db, FakeLLM()):
            assert client.post("/api/scoring/zzzz").status_code == 404

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
        """未配置是**初始状态**，用 200 + null 表达；404 会让人以为是路由错了。"""
        with self._client(db, FakeLLM()) as client:
            response = client.get("/api/scoring/profile")
            assert response.status_code == 200
            assert response.json()["profile"] is None

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
