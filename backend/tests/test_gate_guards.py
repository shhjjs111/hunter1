"""门禁脚本自身的守卫 —— 防「假绿」的回归护栏。

最贵的失败模式不是「脚本报错」，而是**脚本没查却说通过**：本机看到绿、CI 看到红，
两边结论不同源。审查里点名的两个口子都在这里钉住：

- `scripts/check.sh`：缺 shellcheck 的那一节没跑，末尾却照样喊「全部通过」并 exit 0；
- `scripts/contracts.sh`：参数拼错（`--chek`）时落进「写快照」分支 —— 想验漂移的人
  反而把漂移抹平了，还以为自己在做只读检查。

前者跑一次要三分钟（含前端构建），所以用**源码级不变量**钉住判据的形状
（与 `test_lint_judgment.py` 钉 ruff 判定同一手法）；后者便宜，直接真跑。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK_SH = ROOT / "scripts" / "check.sh"
CONTRACTS_SH = ROOT / "scripts" / "contracts.sh"
SNAPSHOT = ROOT / "contracts" / "openapi.json"

#: 跑真的门禁脚本时要给它一个解释器：本机 python 不在 PATH 上（embeddable 版），
#: `contracts.sh` 只在 `$ROOT/.tools/python` 存在时才用它 —— 临时树里没有那个目录。
_PYTHON = ROOT / ".tools" / "python" / "python.exe"


#: 假装参数写错 / 多给参数时，契约脚本必须拒绝执行
UNKNOWN_FLAG_CASES = (["--chek"], ["--check", "extra"])


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("argv", UNKNOWN_FLAG_CASES)
def test_contracts_sh_refuses_bad_arguments(argv: list[str]) -> None:
    """拼错的参数必须报错退出，且**一个字节都不能改**快照。

    原先的 `else` 分支是「重新生成并覆盖已入库的快照」：`--chek` 会静默把漂移抹平，
    而使用者以为自己只是在做只读检查。
    """
    if not SNAPSHOT.is_file():
        pytest.skip("快照不存在（契约尚未导出）")  # 换到干净的 checkout 上也成立
    before = _digest(SNAPSHOT)

    proc = subprocess.run(
        ["bash", str(CONTRACTS_SH), *argv],
        capture_output=True,
        text=True,
        # 显式 utf-8：`text=True` 不给 encoding 时按**宿主 locale** 解码，Windows
        # 简中是 cp936，而 contracts.sh 的拒绝文案含 UTF-8 的 `✗`（E2 9C 97）——
        # reader 线程解码失败 → stdout/stderr 变 None → 下一行 `+` 抛 TypeError。
        # 本机红、CI（utf-8）绿，正是本文件开头说「两边结论不同源」的同类缺口。
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=ROOT,
    )

    assert proc.returncode == 2, f"未知参数应 exit 2，实际 {proc.returncode}"
    out = proc.stdout + proc.stderr
    assert "未知参数" in out or "参数过多" in out
    assert _digest(SNAPSHOT) == before, "拒绝执行时快照被改写了"


def test_contracts_sh_reports_a_skipped_frontend_check(tmp_path: Path) -> None:
    """缺 `frontend/package.json` 时**必须说出来**，不能照喊「✓ 契约零漂移」。

    整段前端类型检查原先包在 `if -f frontend/package.json` 里：缺这个文件时脚本照样
    打印「✓ 契约零漂移」并 exit 0 —— 一个自信的成功行底下少了一半门禁，且没有任何
    一行说「没查」。判据用**真跑**（在临时目录里复制一棵没有 `frontend/` 的树），
    因为「退出码是不是 3」是源码级断言钉不住的那一半。
    """
    if not SNAPSHOT.is_file():
        pytest.skip("快照不存在（契约尚未导出）")
    python = str(_PYTHON) if _PYTHON.exists() else sys.executable

    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(CONTRACTS_SH, root / "scripts" / "contracts.sh")
    shutil.copy(ROOT / "scripts" / "export_openapi.py", root / "scripts" / "export_openapi.py")
    (root / "contracts").mkdir()
    shutil.copy(SNAPSHOT, root / "contracts" / "openapi.json")
    shutil.copytree(ROOT / "backend" / "src", root / "backend" / "src")
    # 刻意**不**建 frontend/ —— 这就是要复现的场景

    proc = subprocess.run(
        ["bash", str(root / "scripts" / "contracts.sh"), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        cwd=root,
        env={**os.environ, "PY": python},
    )
    out = proc.stdout + proc.stderr

    assert proc.returncode == 3, f"应 exit 3（「没查」≠ 通过），实际 {proc.returncode}：\n{out}"
    assert "未执行" in out, f"没有任何一行说「没查」：\n{out}"
    assert "前端" in out, f"没点名是哪一项没跑：\n{out}"


def test_check_sh_cannot_say_all_green_after_skipping() -> None:
    """`check.sh` 的「全部通过」必须在未执行检查的计数之后才可能打印。

    缺 shellcheck 的那一节会打印「未执行」并 `SKIPPED+1`；末尾若直接喊通过，
    本机就是假绿。判据的形状在这里钉死（真跑一次要三分钟，故做源码级断言）：
    ① 有节会累加 SKIPPED；② 末段以 `exit 3` 拦截；③ 那句「全部通过」在拦截之后。
    """
    source = CHECK_SH.read_text(encoding="utf-8")

    assert source.count("SKIPPED=$((SKIPPED + 1))") >= 1, "没有任何一节会登记「未执行」"
    guard_at = source.index('if [[ "$SKIPPED" -gt 0 ]]')
    green_at = source.index('echo "== 全部通过 =="')
    assert guard_at < green_at, "「全部通过」出现在了 SKIPPED 拦截之前"
    assert "exit 3" in source[guard_at:green_at], "SKIPPED 非零时没有以独立退出码拦截"
