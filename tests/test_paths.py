"""运行期路径策略测试。

「数据放哪」在打包前后含义不同：开发时 `sys.executable` 指解释器，
打包后指 exe 自己。混用会把用户数据丢到意料之外的地方 —— 而且是在
**用户机器上**才暴露。所以这里把两种形态都钉住。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


class TestInstalledLayout:
    """`pip install .`（非 editable，src 布局被展平到 site-packages）这一态。

    这一格原先漏了，后果不轻：`__file__` 变成
    `.../site-packages/hunter1/paths.py`，上溯两级是 `site-packages` 的父目录
    （即 `Lib/`）—— 数据库会往 `Lib/.data/` 写，那里通常要管理员权限，
    要么直接崩、要么污染安装目录。而 README 把 pip install 写成正式安装路径。
    """

    def _installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """模拟安装态：没有仓库根可上溯。"""
        monkeypatch.setattr(paths, "_repo_root", lambda: None)

    def test_repo_root_is_detected_in_development(self) -> None:
        # 开发态的对照：确实能找到仓库根
        assert paths._repo_root() is not None

    def test_data_dir_falls_back_to_user_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._installed(monkeypatch)
        assert paths.data_dir() == paths.user_data_dir()

    def test_data_dir_is_not_inside_the_install_tree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """核心断言：不得落在包所在目录（以及它的父目录）里。"""
        self._installed(monkeypatch)
        package_dir = Path(paths.__file__).resolve().parent
        chosen = paths.data_dir()
        assert not chosen.is_relative_to(package_dir)
        assert not chosen.is_relative_to(package_dir.parent)

    def test_installed_default_db_path_is_under_user_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._installed(monkeypatch)
        assert paths.default_db_path() == paths.user_data_dir() / "hunter1.db"


class TestUserDataDir:
    def _force(self, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
        # 改 `_platform_key` 而不是 `os.name`：后者会被 pathlib 在调用时读到，
        # 导致 `Path(...)` 变成当前系统无法实例化的类。
        monkeypatch.setattr(paths, "_platform_key", lambda: key)

    def test_windows_uses_local_appdata(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._force(monkeypatch, "windows")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert paths.user_data_dir() == tmp_path / "Local" / "hunter1"

    def test_windows_falls_back_when_env_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._force(monkeypatch, "windows")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
        assert paths.user_data_dir() == tmp_path / "AppData" / "Local" / "hunter1"

    def test_linux_uses_xdg_data_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._force(monkeypatch, "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
        assert paths.user_data_dir() == tmp_path / "share" / "hunter1"

    def test_linux_falls_back_to_home_dot_local(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._force(monkeypatch, "linux")
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
        assert paths.user_data_dir() == tmp_path / ".local" / "share" / "hunter1"

    def test_macos_uses_application_support(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._force(monkeypatch, "macos")
        monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
        assert paths.user_data_dir() == tmp_path / "Library" / "Application Support" / "hunter1"

    def test_is_named_after_the_app(self) -> None:
        """目录名必须是 hunter1 —— 落进共享的用户数据根目录时靠它区分。"""
        assert paths.user_data_dir().name == "hunter1"

    def test_does_not_create_anything(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """只算路径，不落盘 —— 创建时机由调用方决定。"""
        self._force(monkeypatch, "windows")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        paths.user_data_dir()
        assert not (tmp_path / "Local").exists()
