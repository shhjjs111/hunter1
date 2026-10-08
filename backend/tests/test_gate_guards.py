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
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK_SH = ROOT / "scripts" / "check.sh"
CONTRACTS_SH = ROOT / "scripts" / "contracts.sh"
SNAPSHOT = ROOT / "contracts" / "openapi.json"

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
        timeout=120,
        cwd=ROOT,
    )

    assert proc.returncode == 2, f"未知参数应 exit 2，实际 {proc.returncode}"
    out = proc.stdout + proc.stderr
    assert "未知参数" in out or "参数过多" in out
    assert _digest(SNAPSHOT) == before, "拒绝执行时快照被改写了"


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
