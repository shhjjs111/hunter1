#!/usr/bin/env python
"""产物命名 —— 发布链的**唯一**来源。

为什么单开一个模块：产物压缩包名与可执行文件名被 `build.py`、`release.sh`、
`gh_publish.py` 三处消费，而它们此前各自写死 `hunter1-win32.zip` / `hunter1.exe`。
可这套命名**本就随平台变**（`build.py` 按 `sys.platform` 打包，见下方 `platform_key`）。
后果是整条发布链在非 Windows 上 100% 失败：`release.sh` 找不到产物、`gh_publish.py`
报「缺少产物」。而 CI 跑在 ubuntu-latest —— 这个缺口永远不会被本机门禁发现，
同时 `pyproject.toml` 又自称跨平台。

刻意只用标准库：`gh_publish.py` 是双击发布入口，不该为了取一个文件名就被拉进
`httpx` 这类重依赖（那份依赖只有构建机才需要）。
"""

from __future__ import annotations

import sys

APP_NAME = "hunter1"

#: PyInstaller 产物目录名（`--name`），也是 zip 里的单根目录名。
DIST_DIR_NAME = APP_NAME


def platform_key(platform: str | None = None) -> str:
    """平台词汇 —— 与 `sys.platform` 一致（`win32` / `darwin` / `linux`）。

    必须在构造产物名与更新清单时用**同一个**词汇：清单里的 `platform` 字段要和
    消费端 `sys.platform` 对得上，否则用户永远「该版本没有本平台产物」。
    """
    return (platform or sys.platform).strip()


def artifact_name(platform: str | None = None) -> str:
    """产物压缩包名：`hunter1-<platform>.zip`。"""
    return f"{APP_NAME}-{platform_key(platform)}.zip"


def exe_name(platform: str | None = None) -> str:
    """产物可执行文件名（Windows 带 `.exe`，其余平台没有后缀）。

    `cygwin` 也带 `.exe`（那是 Windows 上的 Python）；判据用前缀而不是
    `os.name == "nt"`，这样传入显式的 platform 字符串时也能得出正确结论 ——
    构建脚本要支持「为另一个平台命名」，只认当前进程的 `os.name` 做不到。
    """
    key = platform_key(platform)
    return f"{APP_NAME}.exe" if key.startswith(("win", "cygwin")) else APP_NAME


def exe_relative_path(platform: str | None = None) -> str:
    """可执行文件相对 `dist/` 的路径：`hunter1/hunter1.exe`（发布脚本用）。"""
    return f"{DIST_DIR_NAME}/{exe_name(platform)}"


__all__ = [
    "APP_NAME",
    "DIST_DIR_NAME",
    "artifact_name",
    "exe_name",
    "exe_relative_path",
    "platform_key",
]
