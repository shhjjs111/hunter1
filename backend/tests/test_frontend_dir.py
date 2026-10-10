"""`main.frontend_dir()` 的三条解析分支。

原先这三条分支在测试里 0 覆盖（只有「打了桩」的集成用例），于是「打包态指到
`_MEIPASS`」「源码态指到仓库 `frontend/dist`」这类改动没有任何测试拦得住 ——
而它们决定 SPA 能不能被伺服：指错就是整站 404（或更糟：伺服了别的目录）。

解析顺序：`HUNTER1_FRONTEND_DIR` → 打包态 `_MEIPASS/hunter1/web_dist` → 源码态
仓库 `frontend/dist`。任一层**不可用**时返回 None（开发态的正常情形），而不是把
不可用的路径当成功 —— 判据是 `index.html` 在不在，不只是「目录存在」：
目录在而 `index.html` 不在时，SPA 回落的 `FileResponse` 会让**每个非 API 路径都
变成 500**，而那条带构建指引的 503 恰恰不会出现。
"""

from __future__ import annotations

import sys
from pathlib import Path

import hunter1.main as main_module
from hunter1.main import frontend_dir


def _dist(path: Path) -> Path:
    """造一个**能伺服**的产物目录：判据是 `index.html`（见 `main._usable_dist`）。"""
    (path / "assets").mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text("<html><div id=root></div></html>", encoding="utf-8")
    return path


class TestEnvOverride:
    def test_env_override_wins(self, monkeypatch, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        _dist(dist)
        monkeypatch.setenv("HUNTER1_FRONTEND_DIR", str(dist))
        assert frontend_dir() == dist

    def test_env_override_pointing_nowhere_is_none(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("HUNTER1_FRONTEND_DIR", str(tmp_path / "absent"))
        assert frontend_dir() is None

    def test_env_override_without_index_html_is_none(self, monkeypatch, tmp_path: Path) -> None:
        """目录在、`index.html` 不在 → None（当成没有产物）。

        回归护栏：原先只判 `is_dir()`，这条路径会让 SPA 回落的 `FileResponse`
        抛 `RuntimeError` —— 每个非 API 路径 500，而构建指引一个字都不显示。
        半截构建 / `HUNTER1_FRONTEND_DIR` 指错 / 打包产物不完整都会落到这里。
        """
        half = tmp_path / "half-built"
        (half / "assets").mkdir(parents=True)
        monkeypatch.setenv("HUNTER1_FRONTEND_DIR", str(half))
        assert frontend_dir() is None

    def test_env_override_beats_bundled_dir(self, monkeypatch, tmp_path: Path) -> None:
        """显式覆盖优先于打包态探测 —— 联调时要能指向任意一份构建产物。"""
        override = tmp_path / "override"
        _dist(override)
        bundled = tmp_path / "bundle"
        _dist(bundled / "hunter1" / "web_dist")
        monkeypatch.setenv("HUNTER1_FRONTEND_DIR", str(override))
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(bundled), raising=False)
        assert frontend_dir() == override


class TestFrozenLayout:
    def _frozen(self, monkeypatch, meipass: Path | None) -> None:
        monkeypatch.delenv("HUNTER1_FRONTEND_DIR", raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        if meipass is None:
            monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        else:
            monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    def test_frozen_uses_meipass(self, monkeypatch, tmp_path: Path) -> None:
        bundled = tmp_path / "mei123"
        web_dist = bundled / "hunter1" / "web_dist"
        _dist(web_dist)
        self._frozen(monkeypatch, bundled)
        assert frontend_dir() == web_dist

    def test_frozen_without_meipass_uses_exe_dir(self, monkeypatch, tmp_path: Path) -> None:
        """PyInstaller 没给 `_MEIPASS` 的形态（如 onedir）也要认得。"""
        exe_dir = tmp_path / "app"
        web_dist = exe_dir / "hunter1" / "web_dist"
        _dist(web_dist)
        monkeypatch.setattr(sys, "executable", str(exe_dir / "hunter1.exe"))
        self._frozen(monkeypatch, None)
        assert frontend_dir() == web_dist

    def test_frozen_without_web_dist_is_none(self, monkeypatch, tmp_path: Path) -> None:
        bundled = tmp_path / "mei123"
        bundled.mkdir()
        self._frozen(monkeypatch, bundled)
        assert frontend_dir() is None


class TestSourceTree:
    def test_source_layout_points_at_repo_frontend_dist(self, monkeypatch, tmp_path: Path) -> None:
        """源码态：`main.py → hunter1 → src → backend → 仓库根`，再拼 `frontend/dist`。"""
        repo = tmp_path / "repo"
        module_file = repo / "backend" / "src" / "hunter1" / "main.py"
        module_file.parent.mkdir(parents=True)
        web_dist = repo / "frontend" / "dist"
        _dist(web_dist)
        monkeypatch.delenv("HUNTER1_FRONTEND_DIR", raising=False)
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(main_module, "__file__", str(module_file))
        assert frontend_dir() == web_dist

    def test_unbuilt_source_tree_is_none(self, monkeypatch, tmp_path: Path) -> None:
        """开发态没跑过 `npm run build`：返回 None（而不是一个不存在的路径）。"""
        repo = tmp_path / "repo"
        module_file = repo / "backend" / "src" / "hunter1" / "main.py"
        module_file.parent.mkdir(parents=True)
        monkeypatch.delenv("HUNTER1_FRONTEND_DIR", raising=False)
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(main_module, "__file__", str(module_file))
        assert frontend_dir() is None
