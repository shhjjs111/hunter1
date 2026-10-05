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

# tests/web/test_cli.py → 上溯三级是仓库根
ROOT = Path(__file__).resolve().parents[2]


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
        assert db.parent.name == ".data"  # 开发态：仓库根的 .data/
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


class TestMain:
    def test_no_command_prints_help_and_fails(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 1
        assert "Hunter1" in capsys.readouterr().out

    def test_parse_error_for_unknown_command_exits(self) -> None:
        with pytest.raises(SystemExit):
            main(["nonsense"])
