"""运行期路径策略 —— 「数据放哪」只在一个地方决定。

三种运行形态，数据落点各不相同：

| 形态 | 判据 | 数据目录 |
|---|---|---|
| 打包产物 | `sys.frozen` | exe 旁的 `data/`（便携：解压即用、删目录即卸载） |
| 开发态 | 上溯能找到 `pyproject.toml` | 仓库根的 `.data/`（已 gitignore） |
| 已安装 | 两者都不是 | 平台的用户数据目录（`%LOCALAPPDATA%\\hunter1` 等） |

**第三种是最初漏掉的一格**：`pip install .` 会把 src 布局展平到
`site-packages/hunter1/`，此时 `Path(__file__).parents[2]` 是 site-packages
的父目录（`Lib/`）—— 数据会往那里写，通常要管理员权限，要么直接崩、
要么污染安装目录。所以判据不能是「相对 `__file__` 走几级」，而必须是
**「上溯能不能找到仓库根」**；找不到就退回用户数据目录。

`user_data_dir()` 只算路径、不创建目录：创建时机由调用方决定（`Database`
建库时才建），这样「看一眼默认路径」不会产生副作用。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DB_FILENAME = "hunter1.db"
APP_DIR_NAME = "hunter1"
_ROOT_MARKER = "pyproject.toml"


def is_frozen() -> bool:
    """是否运行在打包产物里（PyInstaller 会设置 `sys.frozen`）。"""
    return bool(getattr(sys, "frozen", False))


def _repo_root() -> Path | None:
    """上溯找到含 `pyproject.toml` 的目录；找不到返回 None（= 已安装形态）。

    独立成函数是为了能被测试替换 —— 否则「安装态」这一格没法在开发机上复现。
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / _ROOT_MARKER).is_file():
            return parent
    return None


def _platform_key() -> str:
    """运行平台标识：`windows` / `macos` / `linux`。

    独立成函数是为了能测各平台分支 —— 否则要在三台机器上才能覆盖。
    刻意不把 `os.name` 当判据的替身去 monkeypatch：`pathlib.Path.__new__`
    在**调用时**读 `os.name` 决定具体类，改它会让 `Path(...)` 变出
    当前系统无法实例化的类（Windows 上得到 PosixPath）。
    """
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def user_data_dir() -> Path:
    """平台的用户数据目录下的应用目录。只计算，不创建。"""
    key = _platform_key()
    if key == "windows":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    if key == "macos":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_NAME


def app_dir() -> Path:
    """程序所在目录：打包后是 exe 所在目录，开发态是仓库根。

    已安装形态下没有「程序所在目录」这一概念（代码在 site-packages 里，
    那不是放数据的地方），此时返回包目录本身，仅供诊断显示；
    真正的数据落点由 `data_dir()` 决定。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _repo_root() or Path(__file__).resolve().parent


def data_dir() -> Path:
    """用户数据的默认存放处（不自动创建，由调用方决定时机）。"""
    if is_frozen():
        return app_dir() / "data"
    root = _repo_root()
    if root is not None:
        return root / ".data"
    return user_data_dir()


def default_db_path() -> Path:
    """默认的 SQLite 路径。"""
    return data_dir() / DB_FILENAME


__all__ = [
    "DB_FILENAME",
    "app_dir",
    "data_dir",
    "default_db_path",
    "is_frozen",
    "user_data_dir",
]
