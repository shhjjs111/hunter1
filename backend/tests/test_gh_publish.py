"""令牌读取逻辑的测试。

这里只测**安全敏感的纯逻辑**（令牌从哪来、拿不到时怎么报错）。网络部分
（校验、创建仓库、上传）不在单测范围内 —— 那需要真令牌，不适合进测试套件。

重点守两条不变量：
1. 环境变量优先于文件（便于 CI 注入，不必落盘）；
2. 拿不到令牌时**报错并给出操作方法**，不是静默继续 —— 静默继续会让后续
   请求以匿名身份打出去，得到一堆 404/401，很难倒查到「其实是没配令牌」。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gh_publish_script", ROOT / "scripts" / "gh_publish.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gh = _load()


class TestReadToken:
    def test_env_takes_priority(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "  from_env  ")
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", Path("/nonexistent"))
        assert gh.read_token() == "from_env"

    def test_falls_back_to_file(self, monkeypatch, tmp_path: Path) -> None:
        token_file = tmp_path / ".hunter1_gh_token"
        token_file.write_text("from_file\n", encoding="utf-8")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", token_file)
        assert gh.read_token() == "from_file"

    def test_blank_env_is_ignored_in_favour_of_file(self, monkeypatch, tmp_path: Path) -> None:
        """空的环境变量不该被当成有效令牌（CI 里常见 `GITHUB_TOKEN=` 这种空赋值）。"""
        token_file = tmp_path / ".hunter1_gh_token"
        token_file.write_text("from_file", encoding="utf-8")
        monkeypatch.setenv("GITHUB_TOKEN", "   ")
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", token_file)
        assert gh.read_token() == "from_file"

    def test_missing_everything_raises_with_instructions(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", tmp_path / "absent")
        with pytest.raises(gh.PublishError) as excinfo:
            gh.read_token()
        message = str(excinfo.value)
        assert "gh_setup.sh" in message  # 告诉用户怎么补
        assert "GITHUB_TOKEN" in message


class TestNoTokenLeakInArgv:
    def test_remote_url_is_built_without_token(self) -> None:
        """远端地址里不该出现令牌 —— 那会写进 .git/config 并可被读到。"""
        source = (ROOT / "scripts" / "gh_publish.py").read_text(encoding="utf-8")
        assert "{owner}/{repo}.git" in source  # 地址模板不含凭据占位
        # 令牌只经 GIT_ASKPASS 的临时脚本传递
        assert "GIT_ASKPASS" in source
        assert "HUNTER1_GH_TOKEN" in source


class TestResolveTarget:
    """双击入口靠这个函数决定发到哪 —— 用户不必知道自己的账号名。"""

    def test_owner_repo_wins(self) -> None:
        assert gh.resolve_target("acme/hunter1", "ignored", "ignored") == ("acme", "hunter1")

    def test_falls_back_to_owner_and_repo(self) -> None:
        assert gh.resolve_target("", "acme", "hunter1") == ("acme", "hunter1")

    def test_blank_owner_means_fill_from_token(self) -> None:
        """返回空 owner 是**有意**的：调用方稍后用令牌账号补上。"""
        assert gh.resolve_target("", "", "hunter1") == ("", "hunter1")

    def test_owner_repo_without_slash_is_rejected(self) -> None:
        with pytest.raises(gh.PublishError):
            gh.resolve_target("hunter1", "", "")


class TestPromptForToken:
    """交互输入的**清洗与失败处理**（不碰网络：桩掉 verify_token）。"""

    def test_sanitises_pasted_input(self, monkeypatch, tmp_path: Path) -> None:
        """用户常把 `GITHUB_TOKEN=…` 整行、连带空格一起粘进来。"""
        seen: list[str] = []
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", tmp_path / ".tok")
        monkeypatch.setattr(gh, "verify_token", lambda token: seen.append(token) or "someone")
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: "  GITHUB_TOKEN= abc 123 \n")

        result = gh.prompt_for_token()

        assert seen == ["abc123"]  # 前缀与空白都被剥掉
        assert result == "abc123"

    def test_saves_only_after_verification(self, monkeypatch, tmp_path: Path) -> None:
        target = tmp_path / ".tok"
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", target)
        monkeypatch.setattr(gh, "verify_token", lambda token: "someone")
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: "good-token")

        gh.prompt_for_token()

        assert target.read_text(encoding="utf-8") == "good-token"

    def test_retries_then_raises_without_leaving_a_file(self, monkeypatch, tmp_path: Path) -> None:
        """校验不过就不落盘 —— 否则下次会拿着一个坏令牌去发版。"""
        target = tmp_path / ".tok"
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", target)
        monkeypatch.setattr(gh, "verify_token", lambda token: None)
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: "bad-token")

        with pytest.raises(gh.PublishError):
            gh.prompt_for_token()

        assert not target.exists()

    def test_blank_input_does_not_consume_a_verification(self, monkeypatch, tmp_path: Path) -> None:
        """空回车不算一次尝试 —— 否则手滑三次就把自己锁在外面。"""
        calls: list[str] = []

        def fake_getpass(*args, **kwargs) -> str:
            calls.append("asked")
            return "" if len(calls) == 1 else "real-token"

        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", tmp_path / ".tok")
        monkeypatch.setattr(gh, "verify_token", lambda token: "someone")
        monkeypatch.setattr("getpass.getpass", fake_getpass)

        assert gh.prompt_for_token() == "real-token"
        assert len(calls) == 2

    def test_eof_raises_cleanly_instead_of_traceback(self, monkeypatch, tmp_path: Path) -> None:
        """stdin 被重定向/关闭时，getpass 抛 EOFError。

        不接的话会吐一段 traceback —— 对「双击就能用」的工具是最差的失败姿态。
        """
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", tmp_path / ".tok")

        def eof(*args, **kwargs):
            raise EOFError

        monkeypatch.setattr("getpass.getpass", eof)

        with pytest.raises(gh.PublishError) as excinfo:
            gh.prompt_for_token()

        assert "终端" in str(excinfo.value)  # 指引，而非堆栈


class TestReadTokenInteractiveGate:
    def test_non_tty_raises_instead_of_hanging(self, monkeypatch, tmp_path: Path) -> None:
        """CI 里没有终端：必须报错，不能停在一个永远等不到输入的提示上。"""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", tmp_path / "absent")
        monkeypatch.setattr(gh.sys.stdin, "isatty", lambda: False)

        with pytest.raises(gh.PublishError) as excinfo:
            gh.read_token()

        assert "GITHUB_TOKEN" in str(excinfo.value)

    def test_interactive_disabled_skips_the_prompt(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", tmp_path / "absent")
        monkeypatch.setattr(gh.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(
            gh, "prompt_for_token", lambda: pytest.fail("不该在 interactive=False 时发问")
        )

        with pytest.raises(gh.PublishError):
            gh.read_token(interactive=False)
