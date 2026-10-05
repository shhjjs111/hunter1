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


def _client(db: Database, llm: FakeLLM) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(build_router(store=ScoreStore(db), llm=llm, profile=PROFILE), prefix="/api")
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
            build_router(store=ScoreStore(db), llm=BrokenLLM(), profile=PROFILE), prefix="/api"
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.post(f"/api/scoring/{JOB_ID}").status_code == 500
