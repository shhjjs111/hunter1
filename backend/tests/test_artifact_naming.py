"""`scripts/artifact.py` 的**跨平台命名**契约测试 —— 纯函数，无需打包。

为什么单独钉它：`artifact.py` 是产物命名的唯一来源，被 `build.py`、`release.sh`、
`gh_publish.py` 三处消费，而命名**本就随平台变**（`exe_name` 只有 Windows 带 `.exe`）。
本工具交付目标是 Windows（CI 也跑 windows-latest、用 `check.sh --with-pack` 真打包），
所以这些平台词汇必须钉死：一旦 Windows 以外会得出 `hunter1.exe`（或反之），
打包会去 `dist/hunter1/hunter1.exe` 找可执行文件而扑空。

此前只测了「当前平台」的取值（`build._exe_name()`），**没有任何用例传显式
platform** —— 而那正是命名链路赖以区分平台的那半。这里补齐：
每种平台的命名都写死期望值，Windows 之外若混进 `.exe` 立刻红灯。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import artifact  # noqa: E402


class TestExeName:
    @pytest.mark.parametrize("platform", ["win32", "cygwin"])
    def test_windows_family_has_exe_suffix(self, platform: str) -> None:
        assert artifact.exe_name(platform) == "hunter1.exe"

    @pytest.mark.parametrize("platform", ["linux", "darwin"])
    def test_non_windows_has_no_suffix(self, platform: str) -> None:
        """非 Windows 不该带 `.exe` —— 带上则跑在别的平台上的打包冒烟会找不到产物。"""
        assert artifact.exe_name(platform) == "hunter1"


class TestArtifactName:
    def test_embeds_platform_verbatim(self) -> None:
        """压缩包名里的平台段与 `sys.platform` 同词汇 —— 清单与消费端据此对齐。"""
        assert artifact.artifact_name("linux") == "hunter1-linux.zip"
        assert artifact.artifact_name("win32") == "hunter1-win32.zip"
        assert artifact.artifact_name("darwin") == "hunter1-darwin.zip"


class TestExeRelativePath:
    def test_layout_matches_producer(self) -> None:
        """可执行文件相对 `dist/` 的路径：`hunter1/<exe>`，目录名与 `DIST_DIR_NAME` 一致。"""
        assert artifact.exe_relative_path("win32") == "hunter1/hunter1.exe"
        assert artifact.exe_relative_path("linux") == "hunter1/hunter1"


class TestPlatformKey:
    def test_explicit_platform_is_used_verbatim(self) -> None:
        assert artifact.platform_key("linux") == "linux"

    def test_default_follows_current_platform(self) -> None:
        assert artifact.platform_key() == sys.platform


class TestNamingVocabularyIsShared:
    """生产者与消费者用**同一套**命名词汇 —— 否则「该版本没有本平台产物」。

    `build.py` 落 `dist/<DIST_DIR_NAME>/<exe_name()>`，`gh_publish.py` /
    `make_manifest.py` 按 `artifact_name(platform)` / `exe_relative_path(platform)`
    找它。这一组往返断言确保三者不会各自漂移。
    """

    def test_namespace_round_trips_with_current_platform(self) -> None:
        key = artifact.platform_key()
        assert artifact.artifact_name(key) == f"hunter1-{key}.zip"
        assert artifact.exe_relative_path(key).startswith(f"{artifact.DIST_DIR_NAME}/")

    def test_build_script_exe_name_agrees_with_artifact(self) -> None:
        """`build.py` 的本地包装 `_exe_name()` 不得与 `artifact.exe_name()` 漂移。"""
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(
            "build_script_for_artifact_test", ROOT / "scripts" / "build.py"
        )
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module._exe_name() == artifact.exe_name()
