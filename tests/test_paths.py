"""运行期路径策略测试。

「数据放哪」在打包前后含义不同：开发时 `sys.executable` 指解释器，
打包后指 exe 自己。混用会把用户数据丢到意料之外的地方 —— 而且是在
**用户机器上**才暴露。所以这里把两种形态都钉住。
"""

from __future__ import annotations

import sys
from pathlib import Path

from hunter1 import paths


class TestDevelopmentLayout:
    def test_not_frozen_when_running_from_source(self) -> None:
        assert paths.is_frozen() is False

    def test_app_dir_is_repository_root(self) -> None:
        # src/hunter1/paths.py → 上三级是仓库根
        assert (paths.app_dir() / "pyproject.toml").is_file()

    def test_data_dir_is_repository_local_dot_data(self) -> None:
        """开发时的数据目录是仓库根的 `.data/`（已在 .gitignore 里）。"""
        assert paths.data_dir() == paths.app_dir() / ".data"
        assert paths.data_dir().name == ".data"


class TestFrozenLayout:
    def test_frozen_uses_executable_directory(self, monkeypatch, tmp_path: Path) -> None:
        app = tmp_path / "hunter1"
        app.mkdir()
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(app / "hunter1.exe"))

        assert paths.is_frozen() is True
        assert paths.app_dir() == app

    def test_data_dir_sits_next_to_the_executable(self, monkeypatch, tmp_path: Path) -> None:
        """便携优先：数据放程序旁边，解压即用、删目录即卸载。"""
        app = tmp_path / "hunter1"
        app.mkdir()
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(app / "hunter1.exe"))

        assert paths.data_dir() == app / "data"

    def test_frozen_data_dir_is_not_the_dot_data_name(self, monkeypatch, tmp_path: Path) -> None:
        """打包后是给人看的目录名，不该沿用开发时的隐藏目录名。"""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "hunter1.exe"))
        assert paths.data_dir().name == "data"


class TestDefaultDbPath:
    def test_db_lives_in_data_dir(self) -> None:
        assert paths.default_db_path() == paths.data_dir() / "hunter1.db"

    def test_is_a_path_object(self) -> None:
        assert isinstance(paths.default_db_path(), Path)
