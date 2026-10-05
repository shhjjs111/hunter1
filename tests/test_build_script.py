"""构建脚本的校验逻辑测试。

这里盯的是「`verify()` 会不会放过一个跑不起来的产物」：
文件在、体积对、模板在，都不代表它能跑。缺 DLL、C 扩展没打进去、import 链断裂
—— 只有真执行才知道。所以校验被拆成两半，各自可测：

- `layout_problems()`：只看长得对不对，不执行；
- `smoke_run()`：真跑一次 `--help`。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_build_module() -> ModuleType:
    """按路径加载脚本（scripts/ 不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("build_script", ROOT / "scripts" / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build_module()
EXE_NAME = "hunter1.exe" if os.name == "nt" else "hunter1"


def _make_dist(tmp_path: Path, *, exe: bool = True, templates: str = "v6") -> Path:
    dist = tmp_path / "dist" / "hunter1"
    dist.mkdir(parents=True)
    if exe:
        (dist / EXE_NAME).write_bytes(b"stub")
    if templates == "v6":
        folder = dist / "_internal" / "hunter1" / "web" / "templates"
    elif templates == "v5":
        folder = dist / "hunter1" / "web" / "templates"
    else:
        folder = None
    if folder is not None:
        folder.mkdir(parents=True)
        (folder / "base.html").write_text("<html></html>", encoding="utf-8")
    return dist


class TestLayoutProblems:
    def test_accepts_complete_layout(self, tmp_path: Path) -> None:
        assert build.layout_problems(_make_dist(tmp_path)) == []

    def test_reports_missing_executable(self, tmp_path: Path) -> None:
        problems = build.layout_problems(_make_dist(tmp_path, exe=False))
        assert any("可执行文件" in p for p in problems)

    def test_reports_missing_templates(self, tmp_path: Path) -> None:
        problems = build.layout_problems(_make_dist(tmp_path, templates="none"))
        assert any("模板" in p for p in problems)

    def test_reports_empty_template_directory(self, tmp_path: Path) -> None:
        dist = _make_dist(tmp_path)
        for html in (dist / "_internal" / "hunter1" / "web" / "templates").glob("*.html"):
            html.unlink()
        problems = build.layout_problems(dist)
        assert any("空的" in p for p in problems)

    def test_accepts_pyinstaller_5_flat_layout(self, tmp_path: Path) -> None:
        """5.x 的模板是平铺的 —— 不该把「模板在」误报成「不在」。"""
        assert build.layout_problems(_make_dist(tmp_path, templates="v5")) == []

    def test_reports_oversized_product(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(build, "SIZE_BUDGET_MB", 0)
        problems = build.layout_problems(_make_dist(tmp_path))
        assert any("体积" in p for p in problems)

    def test_does_not_execute_anything(self, tmp_path: Path) -> None:
        """布局检查是纯静态的 —— 产物不可执行时也不该抛。"""
        dist = _make_dist(tmp_path)
        assert build.layout_problems(dist) == []  # 那个 exe 只是个字节桩


class TestSmokeRun:
    def test_real_executable_passes(self) -> None:
        """拿一个确定能跑的 exe 验证冒烟逻辑本身。"""
        assert build.smoke_run(Path(sys.executable)) is None

    def test_reports_non_executable_file(self, tmp_path: Path) -> None:
        fake = tmp_path / EXE_NAME
        fake.write_bytes(b"this is not a program")
        problem = build.smoke_run(fake)
        assert problem is not None
        assert "无法执行" in problem or "运行失败" in problem

    def test_reports_missing_file(self, tmp_path: Path) -> None:
        problem = build.smoke_run(tmp_path / "nope.exe")
        assert problem is not None

    def test_detects_wrong_entry_point(self) -> None:
        """能跑、退出码 0，但没打印帮助 —— 入口不对也要拦。"""
        assert build.looks_like_help("usage: hunter1 [-h] {serve,crawl,update} ...")
        assert not build.looks_like_help("")
        assert not build.looks_like_help("完全无关的输出")


class TestVerify:
    def test_layout_problems_short_circuit_execution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """布局都不对时不该去跑产物 —— 免得把「缺文件」误报成「跑不起来」。"""
        called = {"n": 0}

        def boom(_exe: Path) -> str | None:
            called["n"] += 1
            return "不该被调用"

        monkeypatch.setattr(build, "smoke_run", boom)
        problems = build.verify(_make_dist(tmp_path, templates="none"))
        assert called["n"] == 0
        assert problems

    def test_smoke_failure_becomes_a_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(build, "smoke_run", lambda _exe: "产物运行失败（exit 1）")
        problems = build.verify(_make_dist(tmp_path))
        assert problems == ["产物运行失败（exit 1）"]

    def test_clean_product_has_no_problems(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(build, "smoke_run", lambda _exe: None)
        assert build.verify(_make_dist(tmp_path)) == []


class TestFindTemplates:
    def test_prefers_the_v6_layout(self, tmp_path: Path) -> None:
        dist = _make_dist(tmp_path, templates="v6")
        found = build.find_templates(dist)
        assert found is not None and "_internal" in str(found)

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert build.find_templates(_make_dist(tmp_path, templates="none")) is None
