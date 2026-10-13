"""`platform.console` 与「示例在非 UTF-8 控制台下不崩」的守卫。

背景（真实的 CI 失败，不是假想）：门禁改跑 **windows-latest** 后第一次真实运行就红了 ——
`examples/demo_assistant.py` 的 `print(f"岗位库已就绪：…")` 抛
`UnicodeEncodeError: 'charmap' codec can't encode characters`。根因是 **locale**：
本机（简中 Windows，CP936）能编码中文所以一直绿，GitHub 的英文 runner 控制台是
**cp1252，编不了任何中文**。这与 `cli.py` / `hunter1.spec` / `demo_sites.py` 早就
各自处理过的是同一族问题 —— 只是 `examples/` 里那三个漏了防护。

修法是**一处定义**（`platform.console.enable_utf8_console`）+ 各入口引用，而不是
再抄第 N 份。本文件同时钉住两件事：
1. 共享函数本身的行为（含失败容错）；
2. **端到端**：示例脚本在 cp1252 控制台下必须不崩 —— 这条正是 CI 当初抓到的形态，
   本地用 `PYTHONIOENCODING=cp1252` 就能复现，不必等 CI。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hunter1.platform.console import enable_utf8_console

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "examples"

#: 示例脚本 + 其离线参数。全部可在无网络、无模型 key 下跑完。
_OFFLINE_EXAMPLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("demo_assistant.py", ()),
    ("demo_crawl.py", ("--offline",)),
    ("demo_persist.py", ()),
    ("demo_sites.py", ("--offline",)),
)


class TestEnableUtf8Console:
    """共享函数：容错、不抛错 —— 它处在每个入口最前面，自己崩就等于程序崩。"""

    def test_skips_streams_without_reconfigure(self) -> None:
        enable_utf8_console(io.StringIO(), io.StringIO())  # 不应抛错

    def test_no_streams_does_not_raise(self) -> None:
        enable_utf8_console()

    def test_tolerates_reconfigure_failure(self) -> None:
        class Grumpy:
            def reconfigure(self, **_kwargs: object) -> None:
                raise ValueError("nope")

        enable_utf8_console(Grumpy())  # 吞掉，不往上抛

    def test_is_importable_without_heavy_dependencies(self) -> None:
        """示例/CLI 都要在进程最前面 import 它 —— 不能顺带拖进 sqlalchemy 这类重依赖。

        用一个只导本模块的干净子进程验证：`import hunter1.platform.console` 后，
        `sys.modules` 里不该出现 sqlalchemy / httpx / fastapi。
        """
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(ROOT / 'backend' / 'src')!r}); "
            "import hunter1.platform.console as c; "
            "heavy=[m for m in ('sqlalchemy','httpx','fastapi') if m in sys.modules]; "
            "print(','.join(heavy))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", f"console 模块拖进了重依赖：{result.stdout!r}"


class TestExamplesSurviveNonUtf8Console:
    """示例脚本在**非 UTF-8 控制台**（CI 的英文 Windows runner = cp1252）下必须不崩。

    这正是 `windows-latest` CI 第一次真跑时抓到的失败：本机（CP936）能编码中文、
    一直绿，cp1252 下 `print` 中文直接抛 `UnicodeEncodeError`。用
    `PYTHONIOENCODING=cp1252` 本地复现同一形态 —— 不必等 CI 才发现。
    """

    @pytest.mark.parametrize(("script", "args"), _OFFLINE_EXAMPLES, ids=lambda v: str(v))
    def test_example_runs_under_cp1252(self, script: str, args: tuple[str, ...]) -> None:
        env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
        result = subprocess.run(
            [sys.executable, str(EXAMPLES / script), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=ROOT,
            timeout=180,
            check=False,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        assert "UnicodeEncodeError" not in combined, (
            f"{script} 在 cp1252 控制台下崩了（与 CI windows-latest 同一形态）：\n{combined[-600:]}"
        )
        assert result.returncode == 0, f"{script} 退出码 {result.returncode}：\n{combined[-600:]}"
