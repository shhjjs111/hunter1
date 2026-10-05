"""CLI 参数解析测试 —— 不启动服务器、不联网。"""

from __future__ import annotations

import pytest

from hunter1.cli import DEFAULT_DB, _site_keys, main


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


class TestMain:
    def test_no_command_prints_help_and_fails(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 1
        assert "Hunter1" in capsys.readouterr().out

    def test_default_db_is_gitignored_location(self) -> None:
        assert DEFAULT_DB.startswith(".data/")

    def test_parse_error_for_unknown_command_exits(self) -> None:
        with pytest.raises(SystemExit):
            main(["nonsense"])
