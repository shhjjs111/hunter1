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
import socket
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

# tests/ 在 backend/ 下，而 scripts/build.py 在仓库根 —— 上溯两级。
ROOT = Path(__file__).resolve().parents[2]


def _load_build_module() -> ModuleType:
    """按路径加载脚本（scripts/ 不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("build_script", ROOT / "scripts" / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build_module()
EXE_NAME = "hunter1.exe" if os.name == "nt" else "hunter1"


def _make_dist(tmp_path: Path, *, exe: bool = True, frontend: str = "v6") -> Path:
    """造一个产物目录。`frontend` 控制前端产物的布局（None = 不放）。"""
    dist = tmp_path / "dist" / "hunter1"
    dist.mkdir(parents=True)
    if exe:
        (dist / EXE_NAME).write_bytes(b"stub")
    if frontend == "v6":
        folder = dist / "_internal" / "hunter1" / "web_dist"
    elif frontend == "v5":
        folder = dist / "hunter1" / "web_dist"
    else:
        folder = None
    if folder is not None:
        (folder / "assets").mkdir(parents=True)
        (folder / "index.html").write_text('<html><div id="root"></div></html>', encoding="utf-8")
        (folder / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return dist


class TestLayoutProblems:
    def test_accepts_complete_layout(self, tmp_path: Path) -> None:
        assert build.layout_problems(_make_dist(tmp_path)) == []

    def test_reports_missing_executable(self, tmp_path: Path) -> None:
        problems = build.layout_problems(_make_dist(tmp_path, exe=False))
        assert any("可执行文件" in p for p in problems)

    def test_reports_missing_frontend(self, tmp_path: Path) -> None:
        problems = build.layout_problems(_make_dist(tmp_path, frontend="none"))
        assert any("前端产物" in p for p in problems)

    def test_reports_frontend_without_index(self, tmp_path: Path) -> None:
        """有目录但缺 index.html —— 产物是半截的。"""
        dist = _make_dist(tmp_path)
        (dist / "_internal" / "hunter1" / "web_dist" / "index.html").unlink()
        problems = build.layout_problems(dist)
        assert any("index.html" in p for p in problems)

    def test_reports_missing_assets_dir(self, tmp_path: Path) -> None:
        """index.html 在但 assets/ 没了 —— 界面加载不出脚本，必须拦。"""
        import shutil

        dist = _make_dist(tmp_path)
        shutil.rmtree(dist / "_internal" / "hunter1" / "web_dist" / "assets")
        assert any("assets" in p for p in build.layout_problems(dist))

    def test_accepts_pyinstaller_5_flat_layout(self, tmp_path: Path) -> None:
        """5.x 的产物是平铺的 —— 不该把「产物在」误报成「不在」。"""
        assert build.layout_problems(_make_dist(tmp_path, frontend="v5")) == []

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


class TestSmokeHelp:
    """第一层（快）：`--help`。只负责抓「根本起不来」。"""

    def test_real_executable_passes(self) -> None:
        """拿一个确定能跑的可执行文件验证判据本身（解释器的 --help 也打印 usage）。"""
        assert build.smoke_help(Path(sys.executable)) is None

    def test_reports_non_executable_file(self, tmp_path: Path) -> None:
        fake = tmp_path / EXE_NAME
        fake.write_bytes(b"this is not a program")
        problem = build.smoke_help(fake)
        assert problem is not None
        assert "无法执行" in problem or "运行失败" in problem

    def test_reports_missing_file(self, tmp_path: Path) -> None:
        assert build.smoke_help(tmp_path / "nope.exe") is not None

    def test_detects_wrong_entry_point(self) -> None:
        """能跑、退出码 0，但没打印帮助 —— 入口不对也要拦。"""
        assert build.looks_like_help("usage: hunter1 [-h] {serve,crawl,update} ...")
        assert not build.looks_like_help("")
        assert not build.looks_like_help("完全无关的输出")


class TestSmokeRunLayering:
    def test_help_failure_short_circuits_the_serve_probe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """第一层就不合格时不该再去起服务 —— 省下几秒钟。"""
        monkeypatch.setattr(build, "smoke_help", lambda _exe: "起不来")
        probed = {"n": 0}

        def boom(_exe: Path, **_kw: object) -> str | None:
            probed["n"] += 1
            return None

        monkeypatch.setattr(build, "smoke_serve", boom)
        assert build.smoke_run(tmp_path / EXE_NAME) == "起不来"
        assert probed["n"] == 0

    def test_deep_layer_runs_when_help_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(build, "smoke_help", lambda _exe: None)
        monkeypatch.setattr(build, "smoke_serve", lambda _exe, **_kw: "/ 返回 500")
        assert build.smoke_run(tmp_path / EXE_NAME) == "/ 返回 500"


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
        problems = build.verify(_make_dist(tmp_path, frontend="none"))
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


class TestCheckPages:
    """真正能兑现 docstring 那句承诺的一层：起服务、请求页面、看内容。

    `--help` 走的是 argparse，**早于静态资源挂载** —— 所以前端产物缺失、
    `_internal` 路径错位这类最典型的打包事故，它一个都测不出来。实测过：把
    前端产物目录删掉（保留 `_internal/`），`--help` 仍 exit 0，而实际起服务后
    根路径只会给「前端产物未构建」的 503。
    """

    def _serve(self, handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
        """在临时端口起一个最小 HTTP 服务，产出 base_url。"""
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()

    def _ok_handler(self) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = {
                    "/": '<html><div id="root"></div><script src="/assets/app.js"></script></html>',
                    "/assets/app.js": "console.log(1)",
                    "/api/jobs": '{"items": []}',
                    "/api/crawl/status": '{"running": false}',
                    "/api/settings": "null",
                }.get(self.path, "")
                if not body:
                    self.send_error(404)
                    return
                payload = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                pass  # 别把测试输出搅乱

        return Handler

    def test_all_pages_healthy(self) -> None:
        for base in self._serve(self._ok_handler()):
            assert build.check_pages(base) == []

    def test_flags_500_responses(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_error(500)

            def log_message(self, *_args: object) -> None:
                pass

        for base in self._serve(Handler):
            problems = build.check_pages(base)
            assert problems, "500 必须被报出来 —— 这正是 --help 测不出的那类故障"
            assert any("500" in p for p in problems)

    def test_flags_200_without_expected_content(self) -> None:
        """状态码 200 但内容不对也要拦：模板渲染成空页/错误页时就是这样。"""

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                payload = b"<html>something else entirely</html>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                pass

        for base in self._serve(Handler):
            problems = build.check_pages(base)
            assert problems
            assert any("内容" in p for p in problems)

    def test_flags_unreachable_server(self) -> None:
        problems = build.check_pages("http://127.0.0.1:1", timeout=2.0)
        assert problems

    def test_covers_spa_shell_and_api(self) -> None:
        """冒烟必须同时覆盖 SPA 外壳与 API —— 只请求 / 会漏掉「API 全挂」这类事故。"""
        requested: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requested.append(self.path)
                payload = (
                    b'<html><script src="/assets/app.js"></script></html>'
                    if self.path == "/"
                    else b"{}"
                )
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                pass

        for base in self._serve(Handler):
            build.check_pages(base)
            build.check_frontend_assets(base)
        paths = set(requested)
        assert {"/api/jobs", "/api/crawl/status", "/api/settings"} <= paths
        # 入口脚本由 check_frontend_assets 按 index.html 的引用去取
        assert "/assets/app.js" in paths


class TestFrontendAssetsProbe:
    """入口脚本可达性 —— 前端产物能否被浏览器加载的最强静态证据。

    设计要点：**不直接请求 `/assets/`**。目录路径没有索引文件，StaticFiles
    返回 404 是正确行为；拿它当「资源不可达」的证据会误报（实测踩过：
    打包产物本身完全正常，却被这个探针判成不合格）。
    """

    def _serve(self, handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()

    def test_ok_when_script_reachable(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = (
                    '<html><div id="root"></div><script src="/assets/a.js"></script></html>'
                    if self.path == "/"
                    else "console.log(1)"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        for base in self._serve(Handler):
            assert build.check_frontend_assets(base) == []

    def test_flags_missing_entry_script(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/":
                    body = (
                        b'<html><div id="root"></div><script src="/assets/gone.js"></script></html>'
                    )
                    self.send_response(200)
                else:
                    body = b"nope"
                    self.send_response(404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        for base in self._serve(Handler):
            problems = build.check_frontend_assets(base)
            assert problems and "gone.js" in problems[0]

    def test_flags_html_without_script(self) -> None:
        """HTML 里没有 script 引用 —— 产物不完整（不是「没资源要加载」）。"""

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = b"<html><div id='root'></div></html>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        for base in self._serve(Handler):
            assert build.check_frontend_assets(base)


class TestFreePort:
    def test_returns_a_bindable_port(self) -> None:
        port = build.free_port()
        assert 0 < port < 65536
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))  # 不该抛

    def test_successive_calls_do_not_collide_immediately(self) -> None:
        ports = {build.free_port() for _ in range(5)}
        assert len(ports) > 1


class TestFindFrontendDist:
    def test_prefers_the_v6_layout(self, tmp_path: Path) -> None:
        dist = _make_dist(tmp_path, frontend="v6")
        found = build.find_frontend_dist(dist)
        assert found is not None and "_internal" in str(found)

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert build.find_frontend_dist(_make_dist(tmp_path, frontend="none")) is None


class TestLocalProbesIgnoreProxy:
    """本地冒烟请求不能被代理劫持。

    实测（探针）：开着系统代理时，httpx 默认 `trust_env=True` 会把
    `127.0.0.1` 的请求也送给代理，拿到 502 —— 于是 `check_pages` 报
    「产物不完整」，而产物其实是好的。**关掉代理只是让症状消失，根因还在**：
    任何开着代理的机器跑 `check.sh` / 打包冒烟，都会看到假的失败。

    这里用**环境变量**模拟代理（不依赖真实的系统代理），所以测试在任何机器上
    都稳定可复现 —— 指向一个必然连不上的端口，实现若没禁 trust_env 就会被劫持。
    """

    @pytest.fixture(autouse=True)
    def _bad_proxy_env(self, monkeypatch) -> None:
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, "http://127.0.0.1:1")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)

    def _serve(self, handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()

    def _ok_handler(self) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = {
                    "/": '<html><div id="root"></div><script src="/assets/app.js"></script></html>',
                    "/assets/app.js": "console.log(1)",
                    "/api/jobs": '{"items": []}',
                    "/api/crawl/status": '{"running": false}',
                    "/api/settings": "null",
                }.get(self.path, "")
                if not body:
                    self.send_error(404)
                    return
                payload = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                pass

        return Handler

    def test_check_pages_ignores_proxy(self) -> None:
        for base in self._serve(self._ok_handler()):
            assert build.check_pages(base) == [], "本地页面请求不该走代理"

    def test_check_frontend_assets_ignores_proxy(self) -> None:
        for base in self._serve(self._ok_handler()):
            assert build.check_frontend_assets(base) == [], "本地入口脚本请求不该走代理"

    def test_wait_for_http_ignores_proxy(self) -> None:
        class IdleProcess:
            def poll(self):  # 只用到 poll() is None 这一个语义
                return None

        for base in self._serve(self._ok_handler()):
            assert build.wait_for_http(base, IdleProcess(), timeout=3.0) is True
