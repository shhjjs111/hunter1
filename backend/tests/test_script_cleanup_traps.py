"""脚本的退出码不能被「清理失败」改写。

`scripts/*.sh` 全部是 `set -euo pipefail`。在 `set -e` 下，EXIT trap 里最后一条命令
失败会让**整个脚本**以 1 退出 —— 实测（GNU bash 5.2，验证见
`test_unguarded_exit_trap_is_fatal_under_set_e`）：

    set -euo pipefail; trap "false" EXIT; true          -> exit 1
    set -euo pipefail; trap "false || true" EXIT; true  -> exit 0

（注意精确规则：**不加 `set -e` 时** trap 失败并不改写退出码 —— 我实测过
`trap "false" EXIT; exit 0` → 0。真正把 0 变成 1 的是 `set -e` 与 trap 失败的组合，
外加「trap 里显式 `exit N`」这一种。别把这条记成「bash 会用 trap 的状态覆盖退出码」。）

对 `check.sh` 这种门禁，后果是「所有检查都通过、末尾打印『全部通过』，进程却以 1 退出」：
判错方向比不判更坏，而且**本机红、CI 绿**，所以长期潜伏。

触发条件与 `rm` 的具体实现有关：本机 `rm` 是普通二进制时不触发；一旦 `rm` 被换成
会拒绝某种路径形态（例如带盘符前缀）的包装函数，清理就失败 → 门禁假红。清理失败
本身无所谓的，但它不该顶掉结论 —— 所以每条清理都必须自带 `|| true`。

发现路径：审查报告（`check.sh` 本机假红）。本文件把「清理不许让脚本失败」钉住 ——
光靠「记得写 `|| true`」会在下一个人手里丢。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "scripts").glob("*.sh"))

#: `trap <动作> <信号...>`；动作可能是引号包起来的命令，也可能是一个函数名。
_TRAP_RE = re.compile(r"""^\s*trap\s+(?P<action>'[^']*'|"[^"]*"|\S+)\s+(?P<signals>\S.*)$""")
#: 函数定义（脚本里都是 `name() {` 这一种写法）。
_FUNC_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", re.MULTILINE)

_GUARD = "|| true"


def _scripts() -> list[Path]:
    return SCRIPTS


def _body_of(text: str, name: str) -> str | None:
    """取 `name() { ... }` 的函数体（到第一个顶格 `}` 为止）。"""
    match = _FUNC_RE.search(text)
    while match is not None:
        if match.group("name") == name:
            start = match.end()
            end = text.find("\n}", start)
            return text[start:end] if end != -1 else None
        match = _FUNC_RE.search(text, match.end())
    return None


def _unresolved_actions() -> list[str]:
    """列出「清理动作不带 `|| true`」的 trap，形如 `文件:行: 动作`。"""
    bad: list[str] = []
    for script in _scripts():
        text = script.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = _TRAP_RE.match(line)
            if match is None:
                continue
            signals = match.group("signals")
            # 只关心退出时的清理；`trap - EXIT` 是**撤销** trap，无动作可查。
            if "EXIT" not in signals and "0" not in signals.split():
                continue
            action = match.group("action")
            if action in {"-", "''", '""'}:
                continue
            if action.startswith(("'", '"')):
                if _GUARD not in action:
                    bad.append(f"{script.name}:{lineno}: {action}")
                continue
            # 裸词 = 函数名：守卫可以写在函数体里。
            body = _body_of(text, action)
            if body is None or _GUARD not in body:
                bad.append(f"{script.name}:{lineno}: {action}()（函数体里也没有 {_GUARD}）")
    return bad


@pytest.mark.skipif(not _scripts(), reason="scripts/ 下没有 shell 脚本")
def test_exit_trap_cleanups_cannot_fail_the_script() -> None:
    """每条 EXIT trap 的清理都必须自带 `|| true`。

    否则在 `set -euo pipefail` 下，清理一失败就会把整个脚本判成失败 ——
    `check.sh` 会变成「打印『全部通过』却以 1 退出」。
    """
    bad = _unresolved_actions()
    assert not bad, (
        "这些 EXIT trap 的清理没有 `|| true` —— 清理失败会让脚本（含门禁 check.sh）"
        "以非 0 退出，而它可能整轮检查都通过了：\n  " + "\n  ".join(bad)
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash 才能验证规则前提")
@pytest.mark.parametrize(
    ("action", "expected"),
    [("false", 1), ("false || true", 0)],
    ids=["未加守卫（假红）", "加了守卫"],
)
def test_unguarded_exit_trap_is_fatal_under_set_e(
    tmp_path: Path, action: str, expected: int
) -> None:
    """证明上面那条规则**为什么**存在：`set -e` 下未加守卫的 EXIT trap 会把 0 变成 1。

    这条测试的是 bash 自身的行为，不是本仓库的代码 —— 它的价值是让后来者明白
    `|| true` 不是装饰，别当成噪音删掉。
    """
    script = tmp_path / "probe.sh"
    script.write_bytes(f'set -euo pipefail\ntrap "{action}" EXIT\ntrue\n'.encode())

    proc = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        # 同步省略：本用例只关心退出码。
        timeout=30,
    )
    assert proc.returncode == expected, (
        f"trap 动作为 {action!r} 时退出码应为 {expected}，实际 {proc.returncode}。"
        "bash 的行为变了 —— 请重新确认 test_exit_trap_cleanups_cannot_fail_the_script 的前提。"
    )
