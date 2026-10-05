"""版本号来源的一致性测试。

自更新靠 `__version__` 判断「有没有新版」。如果版本号有两份（`pyproject.toml`
一份、`__init__.py` 一份），它们就靠人工同步 —— 而打包时字面量会被编译进 exe，
「改了 pyproject 忘了改 `__init__.py`」这种错**不会报任何错**，只会让更新判断
长期失准（用户永远等不到更新，或收到不该收的更新）。

所以这里不测「两个值是否相等」（那只是把人工同步换成人工同步），
而是测**只有一个来源**：pyproject 声明动态版本，且指向 `__init__.py`。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from hunter1 import __version__
from hunter1.platform.update import parse_version

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
VERSION_FILE = ROOT / "src" / "hunter1" / "__init__.py"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


class TestSingleSourceOfTruth:
    def test_pyproject_does_not_hardcode_version(self) -> None:
        """写死 version 就等于开了第二个来源 —— 两个来源迟早不一致。"""
        project = _pyproject()["project"]
        assert "version" not in project, (
            "pyproject 里不该写死 version；改用 dynamic + [tool.hatch.version] "
            "从 hunter1/__init__.py 读取"
        )
        assert "version" in project.get("dynamic", [])

    def test_hatch_reads_version_from_the_package(self) -> None:
        configured = _pyproject()["tool"]["hatch"]["version"]["path"]
        assert Path(configured).resolve() == VERSION_FILE.resolve()

    def test_declared_file_actually_defines_the_version(self) -> None:
        assert "__version__" in VERSION_FILE.read_text(encoding="utf-8")


class TestVersionValue:
    def test_is_parseable(self) -> None:
        """自更新用 parse_version 比较；解析不了就静默判成「没有新版」。"""
        assert parse_version(__version__)

    def test_is_not_a_placeholder_range(self) -> None:
        assert "-" not in __version__  # 不是 PEP 440 的区间写法

    def test_matches_the_installed_package(self) -> None:
        """`hunter1.__version__` 必须来自包本身，而不是某个外部常量。"""
        assert isinstance(__version__, str) and __version__.strip() == __version__
