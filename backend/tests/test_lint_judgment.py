"""ruff 判定必须与调用者所在目录无关 —— 否则「门禁红不红」取决于你在哪敲命令。

背景（实测复现过；机制注释见 `scripts/check.sh` 与 `backend/pyproject.toml`）：
配置里的 `src = ["src", "tests"]` 是**相对进程 cwd** 解析的，`--config` 传绝对
路径也改变不了这一点。于是 `hunter1` 的 first-party 归属随 cwd 漂移：

    cwd=backend  → linter.src = ["<repo>/backend/src", "<repo>/backend/tests"]
    cwd=仓库根    → linter.src = []      ← 两项都指向不存在的目录，被丢弃

实测差距：`scripts/refresh_fixtures.py` 的 `from bs4 import ...` 与
`from hunter1...` 连排，backend/ 下报 I001、仓库根下 All checks passed ——
同一份工作区、同一条命令，**判定标准本身**在漂移。CI 恒从仓库根跑，所以
「本机红而 CI 绿」可以同时为真，而本地那条红看起来只像噪音。

本测试把「判定与 cwd 无关」钉成可执行断言：两个 cwd 各跑一次同一条 ruff
命令，要求退出码一致且为 0。删掉 isort 的 `known-first-party` 即变红。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
CONFIG = BACKEND / "pyproject.toml"

# check.sh 用同款命令检查这些仓库根目录（它们不属于后端包）
TARGETS = ("scripts", "examples")

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("ruff") is None,
    reason="ruff 未安装 —— pip install -e '.[dev]'",
)


def _ruff_exit_code(cwd: Path, target: Path) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--config", str(CONFIG), str(target)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode


@pytest.mark.parametrize("target", TARGETS)
def test_ruff_judgment_is_independent_of_cwd(target: str) -> None:
    path = ROOT / target
    from_backend = _ruff_exit_code(BACKEND, path)
    from_root = _ruff_exit_code(ROOT, path)

    flipped = (
        f"{target}/ 的 ruff 判定随 cwd 翻转：cwd=backend → {from_backend}，"
        f"cwd=仓库根 → {from_root}。检查 backend/pyproject.toml 的 "
        "[tool.ruff.lint.isort] known-first-party 与 scripts/check.sh 里"
        "对应步骤的 cwd 钉法。"
    )
    assert from_backend == from_root, flipped
    assert from_backend == 0, f"{target}/ 有 ruff 违规（退出码 {from_backend}）"
