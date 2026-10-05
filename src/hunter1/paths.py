"""运行期路径策略 —— 「数据放哪」只在一个地方决定。

打包前后 `sys.executable` 的含义不同：开发时指解释器，打包后指 exe 自己。
混用会把用户数据丢到意料之外的位置，而且只在**用户机器上**才暴露。

取向是**便携**：打包后数据放程序目录旁边的 `data/`，解压即用、删目录即卸载，
不必让用户去 `%APPDATA%` 里找库文件。开发时用仓库根的 `.data/`（已 gitignore）。
"""

from __future__ import annotations

import sys
from pathlib import Path

DB_FILENAME = "hunter1.db"


def is_frozen() -> bool:
    """是否运行在打包产物里（PyInstaller 会设置 `sys.frozen`）。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """程序所在目录：打包后是 exe 所在目录，开发时是仓库根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # src/hunter1/paths.py → parents[2] 是仓库根
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """用户数据的默认存放处（不自动创建，由调用方决定时机）。"""
    return app_dir() / ("data" if is_frozen() else ".data")


def default_db_path() -> Path:
    """默认的 SQLite 路径。"""
    return data_dir() / DB_FILENAME


__all__ = ["DB_FILENAME", "app_dir", "data_dir", "default_db_path", "is_frozen"]
