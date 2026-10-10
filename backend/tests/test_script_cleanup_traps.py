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

**数目对得上**：被扫的 EXIT trap 目前是 **5 条**（`check.sh` / `contracts.sh` /
`dev.sh` / `gh_setup.sh` / `release.sh`）。凡是在哪里写下具体条数，就得与 `_scripts()`
扫出来的实际数一致 —— 曾有一处写「四条」而实际五条，没人会去数，于是错着留了很久。
新增脚本或新增 trap 时这个清单自己会变，不需要改本文件；但改完值得跑一次
`pytest tests/test_script_cleanup_traps.py` 让 `_unresolved_actions()` 重新过一遍。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "scripts").glob("*.sh"))

#: `trap <动作> <信号...>`；动作可能是引号包起来的命令，也可能是一个函数名。
_TRAP_RE = re.compile(r"""^\s*trap\s+(?P<action>'[^']*'|"[^"]*"|\S+)\s+(?P<signals>\S.*)$""")
#: 函数定义（脚本里都是 `name() {` 这一种写法）。
_FUNC_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", re.MULTILINE)

_GUARD = "|| true"

#: 块结束符：它们本身不会失败，出现在守卫之后无妨（守卫在循环/分支体内时，
#: 其后跟的就是这些收尾词）。
_BLOCK_ENDERS = frozenset({"fi", "done", "esac", "}", ")", ";;"})


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


def _last_command_is_guarded(text: str) -> bool:
    """`text` 的**最后一条命令**是否自带 `|| true`。

    只查「整串含 `|| true`」不够：`'rm -f a || true; rm -f b'` 里真正决定退出码的
    是最后那条 `rm -f b`，它没守卫 —— 而整串确实「含」`|| true`，旧判据会漏判
    （audit a13）。这里取最后一次出现 `|| true` 的位置，要求其**之后**只剩空白、
    整行注释或块结束符（`fi` / `done` / `esac` / `}` …）—— 任何还会执行的残留命令
    都算「守的不是最后一条」。整行注释先剔除：注释里提到 `|| true` 不算数。
    """
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    body = "\n".join(lines)
    idx = body.rfind(_GUARD)
    if idx == -1:
        return False
    tail = body[idx + len(_GUARD) :]
    for raw_line in tail.splitlines():
        line = raw_line.split("#", 1)[0].strip().strip(";").strip()
        if line and line not in _BLOCK_ENDERS:
            return False
    return True


def _unresolved_actions() -> list[str]:
    """列出「清理动作的**最后一条命令**不带 `|| true`」的 trap，形如 `文件:行: 动作`。"""
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
                # 去掉外层引号再判：守卫要在**最后一条命令**上。
                if not _last_command_is_guarded(action[1:-1]):
                    bad.append(f"{script.name}:{lineno}: {action}")
                continue
            # 裸词 = 函数名：守卫要写在函数体的**最后一条命令**上。
            body = _body_of(text, action)
            if body is None or not _last_command_is_guarded(body):
                bad.append(
                    f"{script.name}:{lineno}: {action}()（函数体的最后一条命令没有 {_GUARD}）"
                )
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


class TestLastCommandGuard:
    """audit a13：判定必须落在**最后一条命令**上，不是「整串含 `|| true`」。"""

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("rm -f /tmp/x 2>/dev/null || true", True),
            ("rm -f /tmp/x", False),
            # 守卫只在前一条上 —— 最后那条 `rm -f /tmp/y` 才是改写退出码的元凶。
            ("rm -f /tmp/x || true; rm -f /tmp/y", False),
            # 循环体里的守卫：其后跟的是收尾词 `done`，仍算「最后一条有守卫」。
            ('for p in a b; do\n  rm -f "$p" || true\ndone', True),
            # 注释里提到 `|| true` 不算数。
            ("# 说明里提到 || true\nrm -f /tmp/x", False),
        ],
    )
    def test_last_command_only(self, body: str, expected: bool) -> None:
        assert _last_command_is_guarded(body) is expected


class TestSyntheticTrapViolationIsCaught:
    def test_guard_on_a_non_final_command_is_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """构造一个「守卫没落在最后一条」的脚本，确认扫得出来。"""
        module = sys.modules[__name__]
        script = tmp_path / "bad.sh"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "trap 'rm -f /tmp/ok || true; rm -f /tmp/boom' EXIT\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(module, "SCRIPTS", [script])
        bad = module._unresolved_actions()
        assert bad, "守卫只在倒数第二条上 —— 必须判为未加守卫"
