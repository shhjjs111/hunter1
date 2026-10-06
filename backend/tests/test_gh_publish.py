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
import json
import subprocess
import sys
import urllib.error
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
    """交互输入的**清洗与失败处理**（不碰网络：桩掉 verify_token）。

    本类只覆盖**手动**输入路径，所以用 autouse fixture 把剪贴板显式断开 ——
    否则这些用例会去读**真实**剪贴板（实测踩到过：跑测试时读到了我自己的剪贴板
    内容，还把前 4 位打印进了测试输出）。测试不许碰用户环境，读也不行。
    """

    @pytest.fixture(autouse=True)
    def _no_clipboard(self, monkeypatch) -> None:
        monkeypatch.setattr(gh, "token_from_clipboard", lambda: None)

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


class TestPublishedSha256:
    """从线上清单里取某个平台的 sha256 —— --reupload 的阀门就靠它。"""

    def test_picks_matching_platform(self) -> None:
        manifest = {
            "assets": [
                {"platform": "darwin", "sha256": "aaa"},
                {"platform": "win32", "sha256": "bbb"},
            ]
        }
        assert gh.published_sha256(manifest, "win32") == "bbb"

    def test_missing_platform_is_none(self) -> None:
        assert (
            gh.published_sha256({"assets": [{"platform": "darwin", "sha256": "aaa"}]}, "win32")
            is None
        )

    @pytest.mark.parametrize("manifest", [None, {}, {"assets": []}, {"assets": None}])
    def test_missing_or_broken_manifest_is_none(self, manifest) -> None:
        """取不到线上清单时必须返回 None —— 调用方据此**拒绝**重传，不能放行。"""
        assert gh.published_sha256(manifest, "win32") is None

    def test_ignores_non_dict_assets(self) -> None:
        assert (
            gh.published_sha256(
                {"assets": ["nonsense", {"platform": "win32", "sha256": "ok"}]}, "win32"
            )
            == "ok"
        )

    def test_non_string_sha256_is_none(self) -> None:
        assert (
            gh.published_sha256({"assets": [{"platform": "win32", "sha256": 123}]}, "win32") is None
        )


class TestFetchPublishedManifest:
    def test_returns_parsed_dict(self, monkeypatch) -> None:
        payload = json.dumps({"version": "0.1.0", "assets": []}).encode()

        class FakeResponse:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(gh.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
        assert gh.fetch_published_manifest("acme", "hunter1", "v0.1.0") == {
            "version": "0.1.0",
            "assets": [],
        }

    @pytest.mark.parametrize(
        "boom",
        [
            urllib.error.URLError("no network"),
            urllib.error.HTTPError("u", 404, "nope", None, None),
            ValueError("bad json"),
        ],
    )
    def test_failures_return_none(self, monkeypatch, boom) -> None:
        """任何失败都返回 None —— 调用方据此拒绝重传（fail-closed）。"""

        def raiser(*a, **k):
            raise boom

        monkeypatch.setattr(gh.urllib.request, "urlopen", raiser)
        assert gh.fetch_published_manifest("acme", "hunter1", "v0.1.0") is None


class TestReuploadGuard:
    """`--reupload` 宽免了 tag 校验，就必须由 sha256 一致性顶上。

    这组测试守的是：**任何一条拿不到「同一份产物」证据的路径都不能放行**。
    """

    def _call(self, monkeypatch, *, published, local_hash: str):
        monkeypatch.setattr(gh, "fetch_published_manifest", lambda *a, **k: published)
        monkeypatch.setattr(gh, "file_sha256", lambda _p: local_hash)
        return gh.check_reupload_is_same_artifact("acme", "hunter1", "v0.1.0", Path("x.zip"))

    def test_passes_when_hash_matches(self, monkeypatch) -> None:
        result = self._call(
            monkeypatch,
            published={"assets": [{"platform": "win32", "sha256": "AAA"}]},
            local_hash="AAA",
        )
        assert result == "AAA"

    def test_refuses_when_hash_differs(self, monkeypatch, capsys) -> None:
        result = self._call(
            monkeypatch,
            published={"assets": [{"platform": "win32", "sha256": "AAA"}]},
            local_hash="BBB",
        )
        assert result is None
        assert "拒绝重传" in capsys.readouterr().err

    def test_refuses_when_published_manifest_unavailable(self, monkeypatch, capsys) -> None:
        """拿不到线上清单 → 无法证明是同一份产物 → fail-closed 拒绝。"""
        assert self._call(monkeypatch, published=None, local_hash="BBB") is None
        assert "无法确认" in capsys.readouterr().err

    def test_refuses_when_published_lacks_the_platform(self, monkeypatch) -> None:
        result = self._call(
            monkeypatch,
            published={"assets": [{"platform": "darwin", "sha256": "AAA"}]},
            local_hash="AAA",
        )
        assert result is None


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


class TestLooksLikeAToken:
    """宽松判据：够长 + 无空白。不用前缀白名单拒绝（令牌格式变过好几次）。"""

    @pytest.mark.parametrize(
        "text",
        [
            "ghp_" + "a" * 36,  # classic PAT
            "github_pat_" + "b" * 82,  # fine-grained PAT
            "g" * 20,  # 边界：刚好够长
        ],
    )
    def test_accepts_plausible_tokens(self, text: str) -> None:
        assert gh.looks_like_a_token(text)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "too-short",
            "g" * 19,  # 边界：差一个字符
            "has a space in it aaaaaaaaaaaa",
            "two\nlines\nhere\nand\nhere\nand\nhere",
        ],
    )
    def test_rejects_implausible(self, text: str) -> None:
        assert not gh.looks_like_a_token(text)


class TestTokenFromClipboard:
    """桩掉子进程 —— 测试绝不读写真实剪贴板。"""

    def _fake_run(self, monkeypatch, *, stdout="", returncode=0, raises=None):
        def runner(*args, **kwargs):
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout)

        monkeypatch.setattr(gh.subprocess, "run", runner)

    def test_returns_stripped_text(self, monkeypatch) -> None:
        self._fake_run(monkeypatch, stdout="  ghp_abc  \n")
        assert gh.token_from_clipboard() == "ghp_abc"

    def test_empty_clipboard_is_none(self, monkeypatch) -> None:
        self._fake_run(monkeypatch, stdout="   \n")
        assert gh.token_from_clipboard() is None

    def test_nonzero_exit_is_none(self, monkeypatch) -> None:
        self._fake_run(monkeypatch, returncode=1, stdout="whatever")
        assert gh.token_from_clipboard() is None

    def test_missing_powershell_is_none(self, monkeypatch) -> None:
        """没有 PowerShell / 被策略挡住 —— 只是这条路不通，不该炸整个发布。"""
        self._fake_run(monkeypatch, raises=FileNotFoundError("powershell.exe"))
        assert gh.token_from_clipboard() is None

    def test_timeout_is_none(self, monkeypatch) -> None:
        self._fake_run(monkeypatch, raises=subprocess.TimeoutExpired("powershell.exe", 15))
        assert gh.token_from_clipboard() is None


class TestAcceptClipboardToken:
    """用户报的「粘不进窗口」就靠这条路绕开 —— 必须稳。"""

    def test_enter_accepts_and_saves(self, monkeypatch, tmp_path: Path) -> None:
        target = tmp_path / ".tok"
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", target)
        monkeypatch.setattr(gh, "token_from_clipboard", lambda: "github_pat_from_clipboard01")
        monkeypatch.setattr(gh, "verify_token", lambda token: "someone")
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")  # 直接回车

        assert gh._accept_clipboard_token() == "github_pat_from_clipboard01"
        assert target.read_text(encoding="utf-8") == "github_pat_from_clipboard01"

    def test_n_declines_without_calling_github(self, monkeypatch, tmp_path: Path) -> None:
        """选了 n 就不该发出网络请求，也不该落盘。"""
        target = tmp_path / ".tok"
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", target)
        monkeypatch.setattr(gh, "token_from_clipboard", lambda: "some-other-copied-text-here")
        monkeypatch.setattr(gh, "verify_token", lambda token: pytest.fail("不该发起校验"))
        monkeypatch.setattr("builtins.input", lambda *a, **k: "n")

        assert gh._accept_clipboard_token() is None
        assert not target.exists()

    def test_rejected_token_falls_back_without_saving(self, monkeypatch, tmp_path: Path) -> None:
        target = tmp_path / ".tok"
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", target)
        monkeypatch.setattr(gh, "token_from_clipboard", lambda: "stale-token-not-valid-any")
        monkeypatch.setattr(gh, "verify_token", lambda token: None)
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")

        assert gh._accept_clipboard_token() is None
        assert not target.exists()

    def test_empty_clipboard_never_prompts(self, monkeypatch) -> None:
        """剪贴板空着就直接转手动，不该弹一个没有内容的确认。"""
        monkeypatch.setattr(gh, "token_from_clipboard", lambda: None)
        monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("不该发问"))

        assert gh._accept_clipboard_token() is None

    def test_prompt_for_token_prefers_clipboard(self, monkeypatch, tmp_path: Path) -> None:
        """剪贴板命中时不该走到 getpass（那正是「粘不进去」的那一步）。"""
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", tmp_path / ".tok")
        monkeypatch.setattr(gh, "token_from_clipboard", lambda: "clip-token-abcdefghijklmn")
        monkeypatch.setattr(gh, "verify_token", lambda token: "someone")
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        monkeypatch.setattr(
            "getpass.getpass", lambda *a, **k: pytest.fail("剪贴板已命中，不该再要求粘贴")
        )

        assert gh.prompt_for_token() == "clip-token-abcdefghijklmn"
