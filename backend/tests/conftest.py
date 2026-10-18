"""pytest 共享 fixture。"""

from __future__ import annotations

import os
import shutil
import warnings
from pathlib import Path

import pytest
from starlette.exceptions import StarletteDeprecationWarning

# TestClient 的回退警告发生在测试模块导入阶段，早于 pytest 的
# filterwarnings 配置生效；在 conftest 加载时注册才能让它真正拦截回退。
warnings.filterwarnings("error", category=StarletteDeprecationWarning)


def git_bash() -> str | None:
    """定位一个**真正的 Git Bash**；找不到返回 None。

    为什么不能直接 `subprocess.run(["bash", ...])`：Windows 上 `bash` 这个名字
    有两个来源 —— Git Bash，以及 `C:\\Windows\\System32\\bash.exe`（WSL 的启动器）。
    在**未安装 WSL 发行版**的机器上（如 GitHub 的 windows runner），PATH 里的
    `bash` 解析到后者，一执行就打印 `Windows Subsystem for Linux has no installed
    distributions` 并以非 0 退出（且是 UTF-16 输出）—— 本机（`bash`=Git Bash）则
    一切正常，于是「本机绿、CI 红」。这正是 test_gate_guards / test_script_cleanup_traps
    在 CI（windows-latest）上实测踩到的。

    解析顺序（就高不就低，先具体后兜底）：
    1. Git for Windows 的常见安装位置（CI runner 上是 `C:\\Program Files\\Git\\...`）；
    2. 由 `git` 的位置推导（git 在 `<root>/cmd/git.exe` 或 `<root>/mingw64/bin/git.exe`，
       bash 在 `<root>/bin/bash.exe` 或 `<root>/usr/bin/bash.exe`——本机即此形态）；
    3. PATH 上的 `bash`，但**排除 System32 那个 WSL 启动器**。
    """
    if os.name != "nt":
        return shutil.which("bash") or "bash"

    candidates: list[Path] = []
    for env_root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if env_root:
            git_root = Path(env_root) / "Git"
            candidates += [git_root / "bin" / "bash.exe", git_root / "usr" / "bin" / "bash.exe"]

    git = shutil.which("git")
    if git:
        resolved = Path(git).resolve()
        for ancestor in (
            resolved.parents[1] if len(resolved.parents) > 1 else None,
            resolved.parents[2] if len(resolved.parents) > 2 else None,
        ):
            if ancestor is not None:
                candidates += [ancestor / "bin" / "bash.exe", ancestor / "usr" / "bin" / "bash.exe"]

    which_bash = shutil.which("bash")
    if which_bash and "system32" not in which_bash.lower():
        candidates.insert(0, Path(which_bash))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


class FakeClock:
    """可手动推进的单调时钟 + 记录 sleep。

    用于让「限流 / 冷却 / 退避」这类时间相关逻辑可以**确定性、零等待**地测试。
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()
