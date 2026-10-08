"""CLI 参数解析测试 —— 不启动服务器、不联网。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from hunter1.cli import (
    _build_parser,
    _enable_utf8_console,
    _onboarding_note,
    _open_browser_later,
    _site_keys,
    main,
)
from hunter1.paths import default_db_path
from hunter1.slices.crawl.service import BatchCrawlResult, CrawlResult

# tests/test_cli.py → 上溯一级是后端工程根（backend/）
ROOT = Path(__file__).resolve().parents[1]


class TestSiteKeys:
    def test_empty_means_all_sites(self) -> None:
        assert _site_keys("") is None
        assert _site_keys("   ") is None

    def test_parses_comma_separated(self) -> None:
        assert _site_keys("zhipin, job910") == ["zhipin", "job910"]

    def test_unknown_site_fails_loudly(self) -> None:
        """写错站点名要立刻报错并列出可选值 —— 不能默默按「全部」跑。"""
        with pytest.raises(SystemExit) as excinfo:
            _site_keys("nope")
        assert "nope" in str(excinfo.value)
        assert "job910" in str(excinfo.value)


class TestServeOptions:
    def test_no_browser_flag(self) -> None:
        args = _build_parser().parse_args(["serve", "--no-browser"])
        assert args.no_browser is True

    def test_browser_is_on_by_default(self) -> None:
        args = _build_parser().parse_args(["serve"])
        assert args.no_browser is False

    def test_default_db_path_is_concrete(self) -> None:
        """断言**具体落点**，而不是拿同一函数算出的两个值互相比对。

        原先写的是 `default_db_path().parent == data_dir()`，而
        `default_db_path()` 的实现就是 `data_dir() / DB_FILENAME` ——
        两边同源，恒为真，什么都没验证到（包括打包后路径对不对）。
        """
        db = default_db_path()
        assert db.name == "hunter1.db"
        assert db.parent.name == ".data"  # 开发态：backend/ 工程根的 .data/
        assert db == ROOT / ".data" / "hunter1.db"

    def test_default_db_is_a_db_file(self) -> None:
        assert default_db_path().suffix == ".db"

    def test_serve_accepts_port_and_sites(self) -> None:
        args = _build_parser().parse_args(["serve", "--port", "9001", "--sites", "job910"])
        assert args.port == 9001
        assert args.sites == "job910"


class TestOnboardingNote:
    """引导该不该出现，判据是「用户能不能用」，不是「库文件在不在」。

    原先只看 `db_path.exists()`，于是最常见的困境恰好没有引导：
    用户建过库、抓过几次，但一直没配 API Key —— 此时 `first_run=False`，
    既不给提示也不开浏览器，而用户此刻最需要知道的就是「你得先去配置页」。
    """

    def test_new_database_gets_a_first_run_note(self) -> None:
        note = _onboarding_note(is_new_db=True, configured=False)
        assert note is not None
        assert "首次运行" in note

    def test_existing_database_without_config_still_gets_a_note(self) -> None:
        note = _onboarding_note(is_new_db=False, configured=False)
        assert note is not None
        assert "配置" in note

    def test_configured_existing_database_gets_no_note(self) -> None:
        assert _onboarding_note(is_new_db=False, configured=True) is None

    def test_new_database_note_wins_over_configured(self) -> None:
        """库刚建好（配置也只可能在这个库里）—— 按首次运行的口径说。"""
        note = _onboarding_note(is_new_db=True, configured=True)
        assert note is not None and "首次运行" in note

    def test_note_points_at_the_settings_page(self) -> None:
        for note in (
            _onboarding_note(is_new_db=True, configured=False),
            _onboarding_note(is_new_db=False, configured=False),
        ):
            assert note is not None and "「配置」页" in note


class TestBrowserTimer:
    def test_timer_is_daemon(self) -> None:
        """非 daemon 的 Timer 会让 Ctrl+C 退出时 join 它 —— 最长卡一个 delay。"""
        timer = _open_browser_later("http://127.0.0.1:1/", delay=30)
        try:
            assert timer.daemon is True
        finally:
            timer.cancel()  # 别在测试里真去开浏览器

    def test_returns_the_timer_so_callers_can_cancel(self) -> None:
        import threading

        timer = _open_browser_later("http://127.0.0.1:1/", delay=30)
        try:
            assert isinstance(timer, threading.Timer)
        finally:
            timer.cancel()


class TestServeReleasesDatabase:
    """`_serve` 必须和 `_crawl` 一样释放数据库连接。

    不释放的代价是可观测的：SQLite 的 WAL 模式下，连接没关就留下
    `-wal` / `-shm` 两个文件（实测：dispose 后只剩 `.db`，不 dispose 则三个都在）。
    这对「删目录即卸载」的便携定位是个瑕疵。

    更关键的是**执行路径**：`uvicorn.run()` 是常驻的，用户按 Ctrl+C 时抛的是
    `KeyboardInterrupt`，正常返回路径根本走不到 —— 所以清理必须放在 `finally`。
    """

    def _free_port(self) -> int:
        """借一个空闲端口号：`--port 1` 在 Linux 上绑不了（特权端口，非 root 报
        EACCES），于是 `_serve` 在预检处就 return 2、根本走不到 uvicorn —— 测试会以
        「DID NOT RAISE」的形式失败，看起来像清理没做（CI 上就是这么暴露的）。"""
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def _served(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: BaseException):
        import uvicorn

        def boom(*_args: object, **_kwargs: object) -> None:
            raise exc

        monkeypatch.setattr(uvicorn, "run", boom)
        db_file = tmp_path / "data" / "h.db"
        port = str(self._free_port())
        return db_file, lambda: main(
            ["serve", "--db", str(db_file), "--no-browser", "--port", port]
        )

    def test_interrupt_still_releases_the_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_file, run = self._served(tmp_path, monkeypatch, KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            run()
        leftovers = sorted(p.name for p in db_file.parent.glob("h.db*"))
        assert leftovers == ["h.db"], f"连接没释放，残留 {leftovers}"

    def test_normal_exit_releases_the_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_file, run = self._served(tmp_path, monkeypatch, SystemExit(0))
        with pytest.raises(SystemExit):
            run()
        leftovers = sorted(p.name for p in db_file.parent.glob("h.db*"))
        assert leftovers == ["h.db"]

    def test_unusable_database_location_reports_readably(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """路径不可用时要给一句人话，而不是裸 traceback 砸到用户脸上。

        与项目里「不静默失败、错误要让人看懂」的取向一致 —— 启动阶段崩栈
        是这同一条要求上的破例。
        """
        blocker = tmp_path / "blocker"
        blocker.write_text("我是个文件，不是目录", encoding="utf-8")
        code = main(["serve", "--db", str(blocker / "sub" / "x.db"), "--no-browser"])
        assert code == 2
        out = capsys.readouterr().out
        assert "数据库" in out and "x.db" in out
        assert "Traceback" not in out


class TestUtf8Console:
    """控制台编码那条路径不能把程序搞挂 —— 显示不好是小事，起不来是大事。"""

    def test_skips_streams_without_reconfigure(self) -> None:
        _enable_utf8_console(io.StringIO(), io.StringIO())  # 不应抛错

    def test_no_streams_does_not_raise(self) -> None:
        _enable_utf8_console()

    def test_tolerates_reconfigure_failure(self) -> None:
        class Grumpy:
            def reconfigure(self, **_kwargs: object) -> None:
                raise ValueError("nope")

        _enable_utf8_console(Grumpy())  # 吞掉，不往上抛


class TestUpdateCommand:
    def test_update_is_a_subcommand(self) -> None:
        args = _build_parser().parse_args(["update"])
        assert args.command == "update"

    def test_download_is_off_by_default(self) -> None:
        """默认只检查 —— 不该在用户没要求时往磁盘写东西。"""
        args = _build_parser().parse_args(["update"])
        assert args.download is False

    def test_download_flag(self) -> None:
        assert _build_parser().parse_args(["update", "--download"]).download is True

    def test_source_can_be_passed(self) -> None:
        args = _build_parser().parse_args(["update", "--source", "https://x/m.json"])
        assert args.source == "https://x/m.json"

    def test_without_source_reports_missing_config(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """没配更新源时明说，而不是发一个必然失败的请求。"""
        monkeypatch.delenv("HUNTER1_UPDATE_SOURCE", raising=False)
        assert main(["update", "--source", ""]) == 2
        assert "更新源" in capsys.readouterr().out

    def test_unparseable_version_reports_instead_of_traceback(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """清单里 version 解析不了时给可判别的错误，而不是让裸 ValueError 冒到用户面前。"""
        monkeypatch.setenv("HUNTER1_UPDATE_SOURCE", "https://x/m.json")

        def boom(**kwargs: object) -> object:
            raise ValueError("无法解析版本号：'release-x'")

        # `_update` 是**函数内**导入 check_for_update 的，所以补丁要打在被导入的
        # 模块上（打在 hunter1.cli 上找不到该属性）。
        monkeypatch.setattr("hunter1.platform.update.check_for_update", boom)
        assert main(["update"]) == 1
        out = capsys.readouterr().out
        assert "manifest_invalid" in out
        assert "Traceback" not in out

    def test_non_zip_download_reports_instead_of_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """端到端：清单指向一个「不是 zip」的产物 → 人话 + exit 1，不是 traceback。

        修复前 `zipfile.BadZipFile`（mro: BadZipFile → Exception，**不是**
        ValueError）直接穿出 CLI 的 `except (DownloadError, ValueError)`。
        """
        import hashlib
        import json
        import sys

        import httpx

        from hunter1.platform.update import ReleaseClient

        payload = b"<html>404 from the mirror</html>"
        manifest = json.dumps(
            {
                "version": "9.9.9",
                "assets": [
                    {
                        "platform": sys.platform,
                        "url": "https://mirror.example.com/w.zip",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("manifest.json"):
                return httpx.Response(200, text=manifest)
            return httpx.Response(200, content=payload)

        monkeypatch.setattr(
            "hunter1.platform.update.ReleaseClient",
            lambda: ReleaseClient(transport=httpx.MockTransport(handler)),
        )
        code = main(
            [
                "update",
                "--source",
                "https://mirror.example.com/manifest.json",
                "--download",
                "--dest",
                str(tmp_path / "updates"),
            ]
        )
        assert code == 1
        out = capsys.readouterr().out
        assert "下载失败" in out
        assert "archive_invalid" in out
        assert "Traceback" not in out


class TestMain:
    def test_no_command_prints_help_and_fails(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 1
        assert "Hunter1" in capsys.readouterr().out

    def test_parse_error_for_unknown_command_exits(self) -> None:
        with pytest.raises(SystemExit):
            main(["nonsense"])


class TestCrawlExitCode:
    """L9：抓取退出码按**站点成败**判，不按抓到条数判。

    修复前是 `return 0 if batch.fetched else 1`：
    - 站点全成功但本轮没有新岗位（都抓过了）→ 误报失败（1）；
    - 5 站挂 4、1 站抓到 3 条 → 误报成功（0），失败被咽掉。

    用**真件** `BatchCrawlResult` / `CrawlResult`（零 IO 的 dataclass）：替身复刻
    一遍语义，真件改了它会照旧全绿，测的是一个不再存在的世界。
    """

    def _result(self, *, ok: bool, fetched: int) -> CrawlResult:
        return CrawlResult(
            company="某公司",
            fetched=fetched,
            created=fetched,
            updated=0,
            error=None if ok else "boom",
        )

    def _run(self, monkeypatch: pytest.MonkeyPatch, batch: BatchCrawlResult) -> int:
        from hunter1 import cli
        from hunter1.slices import crawl as crawl_pkg

        class _FakeDb:
            def jobs(self) -> object:
                return object()

            def dispose(self) -> None:
                pass

        class _FakeContext:
            db = _FakeDb()

            @classmethod
            def default(cls, **_kw: object) -> _FakeContext:
                return cls()

            def crawler_factory(self) -> list[object]:
                return []

        monkeypatch.setattr(cli, "AppContext", _FakeContext)
        # `_crawl` 是**函数内**导入 crawl_all 的，补丁要打在包上
        monkeypatch.setattr(crawl_pkg, "crawl_all", lambda *_a, **_kw: batch)
        return cli._crawl(_build_parser().parse_args(["crawl"]))

    def test_all_sites_ok_with_zero_fetched_is_success(self, monkeypatch) -> None:
        code = self._run(monkeypatch, BatchCrawlResult([self._result(ok=True, fetched=0)]))
        assert code == 0

    def test_partial_site_failure_is_reported(self, monkeypatch) -> None:
        batch = BatchCrawlResult(
            [self._result(ok=True, fetched=3), self._result(ok=False, fetched=0)]
        )
        code = self._run(monkeypatch, batch)
        assert code == 1, "有站点失败必须让退出码非零（哪怕别的站点抓到了）"


class TestCrawlReleasesDatabase:
    """`_crawl` 也必须用 try/finally 释放连接（与 `_serve` 同一约定）。

    原先 dispose 写在正常返回路径上：抓取中途抛异常时，SQLite 的 -wal/-shm
    会留在数据目录里，与「删目录即卸载」的便携定位冲突。
    """

    def test_exception_still_releases_the_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hunter1.slices import crawl as crawl_pkg

        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("抓取中途炸了")

        monkeypatch.setattr(crawl_pkg, "crawl_all", boom)

        db_file = Path(__file__).resolve().parent / "_tmp_cli_release.db"
        db_file.unlink(missing_ok=True)
        try:
            with pytest.raises(RuntimeError):
                main(["crawl", "--db", str(db_file)])
            leftovers = sorted(p.name for p in db_file.parent.glob("_tmp_cli_release.db*"))
            assert leftovers == ["_tmp_cli_release.db"], f"连接没释放，残留 {leftovers}"
        finally:
            for path in db_file.parent.glob("_tmp_cli_release.db*"):
                path.unlink()


class TestPortProbe:
    """端口被占时先给一句人话，**不要**先打印「已启动」再开一个死服务的页面。"""

    def test_occupied_port_reports_readably(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import socket

        import uvicorn

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            port = blocker.getsockname()[1]

            # 预检就该拦下来 —— 走到 uvicorn.run 说明它没起作用
            monkeypatch.setattr(
                uvicorn,
                "run",
                lambda *_a, **_kw: pytest.fail("端口已被占用，不该走到 uvicorn.run"),
            )
            code = main(
                [
                    "serve",
                    "--db",
                    str(tmp_path / "data" / "h.db"),
                    "--port",
                    str(port),
                    "--no-browser",
                ]
            )

        assert code == 2
        out = capsys.readouterr().out
        assert "端口" in out and str(port) in out
        assert "已启动" not in out, "还没绑上就不该宣布启动成功"

    def test_privileged_port_says_permission_not_occupied(self, monkeypatch) -> None:
        """无权绑定 ≠ 已被占用。

        非 root 的 Linux 上绑 <1024 的端口抛 `PermissionError`（同样是 `OSError`）——
        一律说成「端口已被占用」会让人去杀一个根本不存在的进程。CI 上就是这么暴露的：
        测试用 `--port 1`，Windows 上能绑、Linux 上不能。

        这里用假 socket 复现，不依赖跑测试的账号有没有权限。
        """
        import socket as socket_module

        import hunter1.cli as cli

        class DeniedSocket:
            def __enter__(self) -> DeniedSocket:
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

            def bind(self, _address: tuple[str, int]) -> None:
                raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(socket_module, "socket", lambda *_a, **_kw: DeniedSocket())

        problem = cli._port_problem("127.0.0.1", 1)
        assert problem is not None
        assert "无权绑定" in problem
        assert "已被占用" not in problem
