"""CLI 参数解析测试 —— 不启动服务器、不联网。"""

from __future__ import annotations

import io

import pytest

from hunter1.cli import _build_parser, _enable_utf8_console, _site_keys, main
from hunter1.paths import data_dir, default_db_path


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

    def test_default_db_lives_in_data_dir(self) -> None:
        """默认库位置由 paths 决定，随打包形态变化 —— 这里锁住「在数据目录里」。"""
        assert default_db_path().parent == data_dir()

    def test_default_db_is_a_db_file(self) -> None:
        assert default_db_path().suffix == ".db"

    def test_serve_accepts_port_and_sites(self) -> None:
        args = _build_parser().parse_args(["serve", "--port", "9001", "--sites", "job910"])
        assert args.port == 9001
        assert args.sites == "job910"


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
