"""Web 测试共享 fixture：一个装配好的应用 + 假模型 + 真 SQLite 临时库。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hunter1.infrastructure.db import Database
from hunter1.web.app import create_app
from hunter1.web.context import AppContext
from tests.web.helpers import FIXTURES, NOW, FakeFetcher, FakeLLM, seed_jobs


@pytest.fixture()
def app_env(tmp_path: Path) -> Iterator[tuple[TestClient, Database, FakeLLM]]:
    """产出 (客户端, 数据库, 假模型)。

    三方都能拿到，是因为多数用例既要「页面看起来对」，也要「库里确实变了」，
    还要能控制模型的行为（成功 / 报错 / 脚本化工具调用）。
    """
    db = Database(tmp_path / "web.db")
    db.initialize()
    seed_jobs(db)
    llm = FakeLLM()
    context = AppContext(
        db=db,
        fetcher=FakeFetcher((FIXTURES / "gaoxiaojob.html").read_text(encoding="utf-8")),
        llm_factory=lambda settings: llm,
        clock=lambda: NOW,
        site_keys=["gaoxiaojob"],
    )
    app = create_app(context)
    with TestClient(app) as client:
        yield client, db, llm
