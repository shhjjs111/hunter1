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
import os
import subprocess
import sys
import urllib.error
from dataclasses import dataclass, field
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
    def test_token_goes_via_env_not_argv_or_disk(self, monkeypatch) -> None:
        """令牌只经 GIT_ASKPASS 脚本读的**环境变量**传递。

        原先只断言源码里存在 `GIT_ASKPASS` / `HUNTER1_GH_TOKEN` 两个子串 ——
        模板照留、另把令牌拼进 argv（`git push https://token@host/...` 那种）的改法
        照样全绿。这里真跑一次 `git()`，直接检查三处：
        argv 里没有令牌、askpass 脚本里没有令牌、令牌只在环境变量里。
        """

        @dataclass
        class Seen:
            cmd: list[str] = field(default_factory=list)
            env: dict[str, str] = field(default_factory=dict)
            script: str = ""
            askpass: Path | None = None

        seen = Seen()

        def fake_run(cmd, **kwargs):
            seen.cmd = [str(part) for part in cmd]
            env = dict(kwargs.get("env") or {})
            seen.env = {str(k): str(v) for k, v in env.items()}
            seen.askpass = Path(seen.env["GIT_ASKPASS"])
            seen.script = seen.askpass.read_text(encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(gh.subprocess, "run", fake_run)
        gh.git("push", "origin", "HEAD:refs/heads/main", token="ghp_SECRET_TOKEN_123")

        assert "ghp_SECRET_TOKEN_123" not in " ".join(seen.cmd)  # 不进 argv（ps 可见）
        assert "ghp_SECRET_TOKEN_123" not in seen.script  # 不落盘
        assert seen.env["HUNTER1_GH_TOKEN"] == "ghp_SECRET_TOKEN_123"  # 只经环境变量
        assert "x-access-token" in seen.script  # PAT 约定的用户名
        assert seen.askpass is not None and not seen.askpass.exists()  # 用完即删


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
    def _patch_opener(self, monkeypatch, payload: bytes, *, seen: list | None = None) -> None:
        """桩掉 `_opener` —— **故意不桩 `urllib.request.urlopen`**。

        这样写是为了抓住「忘了走代理」这类疏漏：若实现改用裸 urlopen，这个桩就
        不生效，调用会真的出网（慢或失败），测试随之暴露问题。
        """

        class FakeOpener:
            def open(self, request, timeout=None):
                if seen is not None:
                    seen.append(request.full_url)

                class FakeResponse:
                    def read(self):
                        return payload

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                return FakeResponse()

        def fake_opener(proxy):
            if seen is not None:
                seen.append(f"proxy={proxy}")
            return FakeOpener()

        monkeypatch.setattr(gh, "_opener", fake_opener)

    def test_returns_parsed_dict(self, monkeypatch) -> None:
        self._patch_opener(monkeypatch, json.dumps({"version": "0.1.0", "assets": []}).encode())
        assert gh.fetch_published_manifest("acme", "hunter1", "v0.1.0") == {
            "version": "0.1.0",
            "assets": [],
        }

    def test_goes_through_the_proxy_aware_opener(self, monkeypatch) -> None:
        """直连被挡的环境正是需要代理的场景 —— 这里也必须走 `_opener`。

        原实现用裸 `urlopen`，于是 `--reupload` 在那类网络下会报「取不到线上
        清单」（fail-closed 拒绝），而用户会以为是 Release 不存在。
        """
        seen: list = []
        self._patch_opener(monkeypatch, b'{"version": "0.1.0", "assets": []}', seen=seen)
        monkeypatch.setattr(gh, "effective_proxy", lambda: "http://127.0.0.1:7897")

        gh.fetch_published_manifest("acme", "hunter1", "v0.1.0")

        assert "proxy=http://127.0.0.1:7897" in seen, "必须经 effective_proxy() 取代理"
        assert any(u.startswith("https://github.com/") for u in seen), "请求地址应正常"

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

        class BoomOpener:
            def open(self, request, timeout=None):
                raise boom

        monkeypatch.setattr(gh, "_opener", lambda proxy: BoomOpener())
        assert gh.fetch_published_manifest("acme", "hunter1", "v0.1.0") is None


class TestNoRawUrlopenInSource:
    """结构性守卫：源码里不该出现绕过 `_opener` 的裸 urlopen。

    加代理支持时正是漏了 `fetch_published_manifest` —— 当时那条路径没有测试，
    所以这种疏漏只能靠「直接盯着源码」来挡。将来再加网络调用时，这条会提醒你
    接上 `_opener(effective_proxy())`。
    """

    def test_every_network_call_goes_through_the_opener(self) -> None:
        source = (ROOT / "scripts" / "gh_publish.py").read_text(encoding="utf-8")
        body = source.split("def _opener(", 1)[1]  # 跳过 _opener 自己的定义
        assert "urllib.request.urlopen(" not in body, (
            "发现裸 urllib.request.urlopen —— 它不会走代理检测。"
            "请改用 _opener(effective_proxy()).open(...)"
        )


class TestReuploadGuard:
    """`--reupload` 宽免了 tag 校验，就必须由 sha256 一致性顶上。

    这组测试守的是：**任何一条拿不到「同一份产物」证据的路径都不能放行**。

    用例**显式**声明平台（`LOCAL`），不依赖跑测试的机器：守卫查的是「本机这份产物」
    对应的那个附件，而把清单固定成 `win32` 的写法在 CI（ubuntu）上会去找 `linux`、
    查不到 → 本机绿、CI 红（实测这组里就红了 2 条）。
    """

    LOCAL = "win32"

    def _call(self, monkeypatch, *, published, local_hash: str, platform: str | None = None):
        monkeypatch.setattr(gh, "fetch_published_manifest", lambda *a, **k: published)
        monkeypatch.setattr(gh, "file_sha256", lambda _p: local_hash)
        return gh.check_reupload_is_same_artifact(
            "acme", "hunter1", "v0.1.0", Path("x.zip"), platform=platform or self.LOCAL
        )

    def test_default_platform_follows_platform_key(self, monkeypatch) -> None:
        """缺省查的是 `platform_key()` 的结果，而不是某个写死的值。

        用**哨兵平台名**（`x-test-plat`）：把它替换成 platform_key 的返回值，并让清单
        只声明它。任何写死的实现（win32 / linux / darwin…）都查不到 → 红 ——
        而"本机平台恰好等于写死值"的用例是测不出这件事的（实测：写死 win32 的实现在
        本机 Windows 上全绿）。配对的一半：清单只声明别的平台时必须 fail-closed。
        """
        sentinel = "x-test-plat"
        monkeypatch.setattr(gh, "platform_key", lambda: sentinel)
        monkeypatch.setattr(gh, "file_sha256", lambda _p: "AAA")

        def published_with(platform: str) -> None:
            monkeypatch.setattr(
                gh,
                "fetch_published_manifest",
                lambda *a, _p=platform, **k: {"assets": [{"platform": _p, "sha256": "AAA"}]},
            )

        published_with(sentinel)  # = platform_key() 的返回值 → 认它
        assert (
            gh.check_reupload_is_same_artifact("acme", "hunter1", "v0.1.0", Path("x.zip")) == "AAA"
        )
        published_with("win32")  # 别的平台 → 不认
        assert (
            gh.check_reupload_is_same_artifact("acme", "hunter1", "v0.1.0", Path("x.zip")) is None
        )

        # 显式传入的 platform 必须**覆盖**缺省值 —— 否则 `_call` 里的 `LOCAL` 是个死参数，
        # 而"传了没生效"这类退化只在别的平台上才暴露（本机恰好等于缺省值时全绿）。
        published_with("win32")
        assert (
            gh.check_reupload_is_same_artifact(
                "acme", "hunter1", "v0.1.0", Path("x.zip"), platform="win32"
            )
            == "AAA"
        )

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


class TestProxyFromSettings:
    """把 Windows 注册表的代理设置解析成代理 URL。

    为什么需要它：用户的系统代理写在注册表里（`ProxyEnable=1`），浏览器会用，
    但 **Python urllib 不读注册表**。GitHub 直连被挡时，`release.cmd` 就会失败，
    而用户看到的是「连不上」——他不会想到是代理没被用上。
    """

    def test_disabled_means_no_proxy(self) -> None:
        assert gh.proxy_from_settings(0, "127.0.0.1:7897") is None
        assert gh.proxy_from_settings(None, "127.0.0.1:7897") is None

    def test_enabled_with_host_port(self) -> None:
        assert gh.proxy_from_settings(1, "127.0.0.1:7897") == "http://127.0.0.1:7897"

    def test_blank_server_means_no_proxy(self) -> None:
        assert gh.proxy_from_settings(1, "") is None
        assert gh.proxy_from_settings(1, "   ") is None

    def test_per_scheme_form_prefers_https(self) -> None:
        """IE 风格：`http=a:1;https=b:2` —— 我们要的是 https 那条（GitHub 走 https）。"""
        assert gh.proxy_from_settings(1, "http=127.0.0.1:8080;https=127.0.0.1:7897") == (
            "http://127.0.0.1:7897"
        )

    def test_per_scheme_form_falls_back_to_http_entry(self) -> None:
        assert gh.proxy_from_settings(1, "http=127.0.0.1:8080") == "http://127.0.0.1:8080"

    def test_already_has_scheme(self) -> None:
        assert gh.proxy_from_settings(1, "http://127.0.0.1:7897") == "http://127.0.0.1:7897"

    def test_whitespace_is_tolerated(self) -> None:
        assert gh.proxy_from_settings(1, "  127.0.0.1:7897  ") == "http://127.0.0.1:7897"

    def test_bare_host_without_port_is_still_a_proxy(self) -> None:
        assert gh.proxy_from_settings(1, "proxy.corp") == "http://proxy.corp"


class TestEffectiveProxy:
    def test_env_var_wins(self, monkeypatch) -> None:
        """显式设了 HTTPS_PROXY 就听它的 —— 便于临时指向别的代理或本地调试。"""
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
        monkeypatch.setattr(gh, "system_proxy", lambda: pytest.fail("不该去读注册表"))
        assert gh.effective_proxy() == "http://127.0.0.1:9999"

    def test_falls_back_to_system_proxy(self, monkeypatch) -> None:
        for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(gh, "system_proxy", lambda: "http://127.0.0.1:7897")
        assert gh.effective_proxy() == "http://127.0.0.1:7897"

    def test_opt_out_env_disables_everything(self, monkeypatch) -> None:
        """`HUNTER1_NO_PROXY=1` 是逃生口：代理本身有问题时别被它卡死。"""
        monkeypatch.setenv("HUNTER1_NO_PROXY", "1")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
        monkeypatch.setattr(gh, "system_proxy", lambda: "http://127.0.0.1:7897")
        assert gh.effective_proxy() is None

    def test_nothing_configured_is_none(self, monkeypatch) -> None:
        for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "HUNTER1_NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(gh, "system_proxy", lambda: None)
        assert gh.effective_proxy() is None


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


class TestClipboardIsWindowsOnly:
    """`token_from_clipboard` 的**平台契约** —— 这条在所有平台都有意义。

    函数体第一行就是 `if os.name != "nt": return None`（它只读 Windows 剪贴板）。
    改动它之前先看这里：**非 Windows 必须干净地返回 None**，而不是抛错、
    也不是去调一个本机不存在的命令。
    """

    def test_non_windows_returns_none_without_touching_subprocess(self, monkeypatch) -> None:
        if os.name == "nt":
            pytest.skip("本条守的是非 Windows 分支")
        monkeypatch.setattr(gh.subprocess, "run", lambda *a, **k: pytest.fail("不该调用子进程"))
        assert gh.token_from_clipboard() is None


@pytest.mark.skipif(
    os.name != "nt",
    reason="token_from_clipboard 是**仅 Windows** 的实现（函数体第一行就是 os.name 守卫，"
    "非 Windows 直接 return None）。下面这些用例桩掉 subprocess 来验证 PowerShell 输出的"
    "解析，在非 Windows 上永远走不到那段代码。该契约本身由 TestClipboardIsWindowsOnly 守。",
)
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


class TestReleaseErrorBranchingRegression:
    """状态码分流必须按 status，不能按消息文本匹配。

    旧实现用 `"HTTP 422" not in str(exc)` 判断「tag 已存在 Release」——
    任何一条消息里恰好含这几个字符的错误（例如 400 的详情把用户引向 422 文档）
    都会被误当成 422，于是脚本去复用 Release，把真实错误咽掉。
    """

    def test_400_whose_detail_mentions_422_is_not_taken_as_already_exists(
        self, monkeypatch
    ) -> None:
        def fake_api(method, path, token, **kw):
            if method == "GET":
                return {"id": 7}
            raise gh.PublishError(
                f"{method} {path} → HTTP 400：Validation Failed, see HTTP 422 docs"
            )

        monkeypatch.setattr(gh, "api_request", fake_api)
        with pytest.raises(gh.PublishError):
            gh.ensure_release("a", "b", "t", "v1", "notes")


class TestVerifyTokenNetworkRegression:
    """网络故障不能被吞成「令牌无效」—— 两者的下一步完全不同。"""

    def test_network_failure_propagates(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise gh.PublishError("GET /user → 网络不可达：No route to host（已试过代理 x）")

        monkeypatch.setattr(gh, "api_request", boom)
        with pytest.raises(gh.PublishError):
            gh.verify_token("whatever")


class TestClipboardDefaultIsSafe:
    """不像令牌的剪贴板内容，回车默认「跳过」—— 不发给 GitHub。

    误复制的文本（聊天里的一句、随手选的一段）不该靠一个回车就送出去。
    想强行用就显式敲 y：默认动作落在安全的那一侧。
    """

    def test_implausible_text_enter_skips_without_calling_github(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        target = tmp_path / ".tok"
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", target)
        monkeypatch.setattr(gh, "token_from_clipboard", lambda: "short")
        monkeypatch.setattr(gh, "verify_token", lambda t: pytest.fail("不该发起校验"))
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")  # 直接回车

        assert gh._accept_clipboard_token() is None
        assert not target.exists()

    def test_implausible_text_can_still_be_forced_with_y(self, monkeypatch, tmp_path: Path) -> None:
        target = tmp_path / ".tok"
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", target)
        monkeypatch.setattr(gh, "token_from_clipboard", lambda: "short")
        monkeypatch.setattr(gh, "verify_token", lambda t: "someone")
        monkeypatch.setattr("builtins.input", lambda *a, **k: "y")

        assert gh._accept_clipboard_token() == "short"
        assert target.read_text(encoding="utf-8") == "short"


class TestApiErrorCarriesStatus:
    """调用方按 status 分流 —— 不再匹配消息文本。"""

    def test_status_and_message(self) -> None:
        exc = gh.ApiError("POST", "/repos/a/b/releases", 422, "already_exists")
        assert exc.status == 422
        assert "HTTP 422" in str(exc)
        assert "already_exists" in str(exc)

    def test_blank_detail_has_no_dangling_colon(self) -> None:
        assert str(gh.ApiError("GET", "/user", 404)) == "GET /user → HTTP 404"

    def test_both_subclasses_are_publish_errors(self) -> None:
        """既有的 `except PublishError` 兜底必须继续接住它们。"""
        assert issubclass(gh.ApiError, gh.PublishError)
        assert issubclass(gh.NetworkError, gh.PublishError)


class TestVerifyTokenContract:
    def test_network_error_propagates(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise gh.NetworkError("GET /user → 网络不可达：…")

        monkeypatch.setattr(gh, "api_request", boom)
        with pytest.raises(gh.NetworkError):
            gh.verify_token("whatever")

    def test_auth_rejection_is_none(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise gh.ApiError("GET", "/user", 401, "Bad credentials")

        monkeypatch.setattr(gh, "api_request", boom)
        assert gh.verify_token("bad") is None

    def test_success_returns_login(self, monkeypatch) -> None:
        monkeypatch.setattr(gh, "api_request", lambda *a, **k: {"login": "someone"})
        assert gh.verify_token("good") == "someone"


class TestEnsureRepoUsesStatus:
    def test_404_proceeds_to_create(self, monkeypatch) -> None:
        posts: list[str] = []

        def fake_api(method, path, token, **kw):
            if method == "POST":
                posts.append(path)
                return {}
            if path == "/user":
                return {"login": ""}
            raise gh.ApiError("GET", path, 404, "Not Found")

        monkeypatch.setattr(gh, "api_request", fake_api)
        gh.ensure_repo("acme", "hunter1", "t", create=True)
        assert posts == ["/user/repos"]

    def test_500_is_reraised(self, monkeypatch) -> None:
        def fake_api(method, path, token, **kw):
            raise gh.ApiError(method, path, 500, "server error")

        monkeypatch.setattr(gh, "api_request", fake_api)
        with pytest.raises(gh.ApiError):
            gh.ensure_repo("acme", "hunter1", "t", create=False)


class TestEnsureReleaseUsesStatus:
    def test_422_reuses_existing_release(self, monkeypatch) -> None:
        def fake_api(method, path, token, **kw):
            if method == "POST":
                raise gh.ApiError("POST", path, 422, "Validation Failed")
            return {"id": 7}

        monkeypatch.setattr(gh, "api_request", fake_api)
        assert gh.ensure_release("a", "b", "t", "v1", "notes").id == 7

    def test_500_is_reraised(self, monkeypatch) -> None:
        def fake_api(method, path, token, **kw):
            raise gh.ApiError(method, path, 500, "server error")

        monkeypatch.setattr(gh, "api_request", fake_api)
        with pytest.raises(gh.ApiError):
            gh.ensure_release("a", "b", "t", "v1", "notes")


class TestReleaseDraftFlow:
    """发布链的原子性：Release 先建**草稿**，附件传完才 PATCH 公开。

    原先 `ensure_release` 直接 `draft: False`：Release 一建就公开，而上传顺序是
    「zip → manifest」—— zip 成功、manifest 失败时 `releases/latest/download/
    manifest.json` 直接 404，所有用户的更新链同时断掉，而脚本只 return 1、不做清理。
    """

    def test_created_as_draft(self, monkeypatch) -> None:
        seen: list[dict] = []

        def fake_api(method, path, token, **kw):
            seen.append(kw.get("payload") or {})
            return {"id": 5}

        monkeypatch.setattr(gh, "api_request", fake_api)
        assert gh.ensure_release("a", "b", "t", "v1", "notes").id == 5
        assert seen[0]["draft"] is True, "新建的 Release 必须是草稿，附件传完才公开"

    def test_existing_published_release_is_pulled_back_to_draft(self, monkeypatch) -> None:
        """重发 / --reupload：已发布的同名 Release 先收回草稿再动附件。

        收回后 `releases/latest` 回落到上一版 —— 比「先删旧附件」那个 404 窗口安全。
        """
        patches: list[dict] = []

        def fake_api(method, path, token, **kw):
            if method == "POST":
                raise gh.ApiError("POST", path, 422, "already_exists")
            if method == "GET":
                return {"id": 5, "draft": False}
            patches.append(kw.get("payload") or {})
            return {}

        monkeypatch.setattr(gh, "api_request", fake_api)
        handle = gh.ensure_release("a", "b", "t", "v1", "notes")
        assert handle.id == 5
        assert handle.reopened_from_published is True, "收回过已发布版本，失败路径要能据此还原"
        assert patches == [{"draft": True}]

    def test_already_draft_is_not_patched_again(self, monkeypatch) -> None:
        patches: list[dict] = []

        def fake_api(method, path, token, **kw):
            if method == "POST":
                raise gh.ApiError("POST", path, 422, "already_exists")
            if method == "GET":
                return {"id": 5, "draft": True}
            patches.append(kw.get("payload") or {})
            return {}

        monkeypatch.setattr(gh, "api_request", fake_api)
        handle = gh.ensure_release("a", "b", "t", "v1", "notes")
        assert handle.id == 5 and handle.reopened_from_published is False
        assert patches == []

    def test_publish_release_flips_draft_off(self, monkeypatch) -> None:
        seen: list[tuple[str, dict]] = []

        def fake_api(method, path, token, **kw):
            seen.append((method, kw.get("payload") or {}))
            return {}

        monkeypatch.setattr(gh, "api_request", fake_api)
        gh.publish_release("a", "b", "t", 5, "v1")
        assert seen == [("PATCH", {"draft": False})]


class TestVerifyTokenStatus:
    """只有**明确的鉴权失败**（401）才算「令牌被拒」。

    GitHub 自身故障（5xx）、限流（429）与 403 都**不是**令牌问题 —— 原先
    `except ApiError` 一把抓全归成 None，用户会被告知「GitHub 拒绝了令牌」而去
    反复折腾令牌，而真正的问题在 GitHub 那一侧。

    403 尤其危险：GitHub 的主/次**速率限制**都用 403。把正常令牌报成「被拒」，
    与这个模块刻意区分 `NetworkError` 的初衷正好相反（把人指到完全错误的方向）。
    """

    def test_401_is_an_auth_rejection(self, monkeypatch) -> None:
        def boom(method, path, token, **kw):
            raise gh.ApiError(method, path, 401, "Bad credentials")

        monkeypatch.setattr(gh, "api_request", boom)
        assert gh.verify_token("t") is None

    @pytest.mark.parametrize("status", [403, 429, 500, 502])
    def test_github_side_failures_propagate(self, monkeypatch, status: int) -> None:
        detail = "API rate limit exceeded for ..." if status == 403 else "server side"

        def boom(method, path, token, **kw):
            raise gh.ApiError(method, path, status, detail)

        monkeypatch.setattr(gh, "api_request", boom)
        with pytest.raises(gh.PublishError) as excinfo:
            gh.verify_token("t")
        if status == 403:
            # 403 的文案必须**明说**这一点：否则用户又去重建一个完全正常的令牌。
            assert "不等于令牌无效" in str(excinfo.value)


class TestTokenFileEnvOverride:
    """L13：令牌文件位置必须与 scripts/gh_setup.sh 用**同一个**环境变量。

    原先 gh_publish.py 硬编码 `~/.hunter1_gh_token`，而 gh_setup.sh 认
    `HUNTER1_GH_TOKEN_FILE` —— 设了该变量的用户会被告知「已保存」，
    发布脚本却去读另一个文件、读不到。

    行为断言：真设上该变量、重新导入模块，常量必须落在它指的位置。
    （只 grep 源码文本挡不住「名字在、取值写死」这类改法。）
    """

    def test_env_var_decides_token_path_at_import(self, monkeypatch, tmp_path: Path) -> None:
        target = tmp_path / "custom_token"
        monkeypatch.setenv("HUNTER1_GH_TOKEN_FILE", str(target))
        module = _load()  # 常量在导入时求值 —— 重新导入才是真实生效路径
        assert target == module.DEFAULT_TOKEN_FILE

    def test_setup_script_writes_that_same_variable(self) -> None:
        """另一半只能在源码里认名字：gh_setup.sh 是 shell，没有可调用的函数面。"""
        setup = (ROOT / "scripts" / "gh_setup.sh").read_text(encoding="utf-8")
        assert "HUNTER1_GH_TOKEN_FILE" in setup


class TestReuploadCheckOrdering:
    """M9：`--reupload` 的产物校验必须发生在 owner 解析**之后**、dry-run 返回**之前**。

    两个错位都会造成误导：

    - 在 owner 解析前 → owner 为空时拼出 `https://github.com//hunter1/...`
      必然取不到，fail-closed 报「请正常发布一次」，把人指向完全错误的方向
      （而「省略 owner」正是双击入口的默认路径）；
    - 在 dry-run 返回前 → `--dry-run` 也会发一次网络请求，破坏只读承诺。

    断言按**实际调用顺序与实参**，不再按源码文本里两个字符串的 `.index()`：
    后者等价重构就假红（这段挪进辅助函数、或前面多一行打印都会挪位置），
    真错位也可能假绿（字符串先出现在注释/文档串里）。
    """

    @pytest.fixture
    def harness(self, monkeypatch, tmp_path: Path) -> list[tuple[str, tuple[object, ...]]]:
        root = tmp_path / "repo"
        (root / "dist").mkdir(parents=True)
        # 产物名从 scripts/artifact.py 取（本平台派生）—— 写死 win32 会让这条用例
        # 在非 Windows 上假红，而「发布链只认 win32」正是这轮要修的问题。
        (root / "dist" / gh.artifact_name()).write_bytes(b"zip")
        exe = root / "dist" / gh.exe_relative_path()
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"exe")
        monkeypatch.setattr(gh, "ROOT", root)

        calls: list[tuple[str, tuple[object, ...]]] = []

        def fake_run(cmd, **kwargs):
            # `git status --porcelain` 必须干净，`git rev-parse` 给个稳定 sha
            out = "" if "status" in cmd else "cafebabe1234\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")

        monkeypatch.setattr(gh.subprocess, "run", fake_run)
        monkeypatch.setattr(gh, "read_token", lambda: "fake-token")

        def fake_api(method: str, path: str, token: str, **kw: object) -> dict[str, object]:
            calls.append(("api", (method, path)))
            return {"login": "acme", "id": 7}

        monkeypatch.setattr(gh, "api_request", fake_api)

        def fake_check(owner: str, repo: str, tag: str, zip_path: Path) -> str:
            calls.append(("check", (owner, repo)))
            return "a" * 64

        monkeypatch.setattr(gh, "check_reupload_is_same_artifact", fake_check)
        for name in (
            "ensure_repo",
            "regenerate_manifest",
            "upload_asset",
            "publish_release",
        ):
            monkeypatch.setattr(gh, name, lambda *a, **k: None)
        monkeypatch.setattr(gh, "ensure_release", lambda *a, **k: gh.ReleaseHandle(7))
        return calls

    def test_check_sees_the_resolved_owner(
        self, harness: list[tuple[str, tuple[object, ...]]]
    ) -> None:
        # 走「省略 owner」这条路（双击入口的默认路径）：owner 必须由令牌账号补上，
        # 校验拿到的不能是空串。
        assert gh.main(["--reupload"]) == 0
        kinds = [name for name, _ in harness]
        check_args = next(args for name, args in harness if name == "check")
        assert check_args[0] == "acme"
        assert kinds.index("api") < kinds.index("check")

    def test_dry_run_sends_no_request(self, harness: list[tuple[str, tuple[object, ...]]]) -> None:
        # --dry-run 是只读承诺：不解析令牌，也不发产物校验请求
        assert gh.main(["--reupload", "--dry-run"]) == 0
        assert harness == []


class TestPublishIsTheLastStep:
    """端到端的顺序断言：Release 一定在两个附件都传完之后才公开。

    这是「不可逆的对外损坏」那条的回归护栏 —— 只断言 `ensure_release` 的 payload
    不够，真正要钉的是**顺序**（先建草稿 → 传 zip → 传 manifest → 发布）。
    """

    @pytest.fixture
    def steps(self, monkeypatch, tmp_path: Path) -> list[str]:
        root = tmp_path / "repo"
        (root / "dist").mkdir(parents=True)
        (root / "dist" / gh.artifact_name()).write_bytes(b"zip")
        exe = root / "dist" / gh.exe_relative_path()
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"exe")
        monkeypatch.setattr(gh, "ROOT", root)

        def fake_run(cmd, **kwargs):
            out = "" if "status" in cmd else "cafebabe1234\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")

        monkeypatch.setattr(gh.subprocess, "run", fake_run)
        monkeypatch.setattr(gh, "read_token", lambda: "fake-token")
        monkeypatch.setattr(gh, "api_request", lambda *a, **k: {"login": "acme", "id": 7})
        monkeypatch.setattr(gh, "ensure_repo", lambda *a, **k: None)
        monkeypatch.setattr(gh, "push", lambda *a, **k: None)
        monkeypatch.setattr(
            gh, "regenerate_manifest", lambda *a, **k: root / "dist" / "manifest.json"
        )

        order: list[str] = []
        monkeypatch.setattr(
            gh,
            "ensure_release",
            lambda *a, **k: order.append("create-draft") or gh.ReleaseHandle(7),
        )
        monkeypatch.setattr(
            gh,
            "upload_asset",
            lambda _o, _r, _t, _rid, path, **k: order.append(f"upload:{path.name}"),
        )
        monkeypatch.setattr(gh, "publish_release", lambda *a, **k: order.append("publish"))
        return order

    def test_publish_comes_after_both_uploads(self, steps: list[str]) -> None:
        assert gh.main(["--owner", "acme"]) == 0
        assert steps == [
            "create-draft",
            f"upload:{gh.artifact_name()}",
            "upload:manifest.json",
            "publish",
        ], steps


class TestFailedPublishRestoresAReopenedRelease:
    """中途失败时，被「暂时收回为草稿」的**已发布** Release 必须放回去。

    没有这一步：该版本永久滞留草稿（匿名下载地址断供），而脚本只打印错误就退出 ——
    用户唯一能做的是「完整重跑一次并全部成功」，失败信息里没有任何提示。
    """

    def _run(
        self, monkeypatch, tmp_path: Path, *, reopened: bool
    ) -> tuple[int, list[tuple[str, object]]]:
        root = tmp_path / "repo"
        (root / "dist").mkdir(parents=True)
        (root / "dist" / gh.artifact_name()).write_bytes(b"zip")
        exe = root / "dist" / gh.exe_relative_path()
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"exe")
        monkeypatch.setattr(gh, "ROOT", root)

        def fake_run(cmd, **kwargs):
            out = "" if "status" in cmd else "cafebabe1234\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")

        monkeypatch.setattr(gh.subprocess, "run", fake_run)
        monkeypatch.setattr(gh, "read_token", lambda: "fake-token")
        monkeypatch.setattr(gh, "api_request", lambda *a, **k: {"login": "acme", "id": 7})
        monkeypatch.setattr(gh, "ensure_repo", lambda *a, **k: None)
        monkeypatch.setattr(gh, "push", lambda *a, **k: None)
        monkeypatch.setattr(
            gh, "regenerate_manifest", lambda *a, **k: root / "dist" / "manifest.json"
        )
        monkeypatch.setattr(
            gh,
            "ensure_release",
            lambda *a, **k: gh.ReleaseHandle(7, reopened_from_published=reopened),
        )

        def boom(*a: object, **k: object) -> None:
            raise gh.PublishError("上传失败（网络中断）")

        monkeypatch.setattr(gh, "upload_asset", boom)

        seen: list[tuple[str, object]] = []
        monkeypatch.setattr(
            gh, "set_draft", lambda _o, _r, _t, _rid, *, draft: seen.append(("draft", draft))
        )
        monkeypatch.setattr(gh, "publish_release", lambda *a, **k: seen.append(("publish", None)))
        return gh.main(["--owner", "acme"]), seen

    def test_restores_a_released_version_that_was_pulled_back(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        code, seen = self._run(monkeypatch, tmp_path, reopened=True)
        assert code == 1
        assert ("draft", False) in seen, "失败路径必须把收回的 Release 还原为已发布"
        assert ("publish", None) not in seen, "失败时不该走正常发布路径"

    def test_leaves_a_fresh_draft_alone(self, monkeypatch, tmp_path: Path) -> None:
        code, seen = self._run(monkeypatch, tmp_path, reopened=False)
        assert code == 1
        assert seen == [], "新建的草稿不该被公开 —— 半成品留在草稿态即可"


class TestResolveTargetValidation:
    """audit C17：owner/repo 也走字符白名单 —— 入口校验不能比 release.sh 更弱。"""

    @pytest.mark.parametrize(
        "owner_repo",
        ["../repo", "a/..", "a/b/c", "-bad/repo", "/repo", "a/", "a b/repo"],
    )
    def test_malformed_owner_repo_is_rejected(self, owner_repo: str) -> None:
        with pytest.raises(gh.PublishError):
            gh.resolve_target(owner_repo, "", "")

    def test_dot_dot_would_build_a_suspicious_url(self) -> None:
        """`..` 会拼出 `https://github.com/../repo` 这类可疑地址 —— 入口就挡掉。"""
        with pytest.raises(gh.PublishError):
            gh.resolve_target("../x", "", "")

    def test_owner_and_repo_flags_are_validated_too(self) -> None:
        with pytest.raises(gh.PublishError):
            gh.resolve_target("", "..", "hunter1")
        with pytest.raises(gh.PublishError):
            gh.resolve_target("", "acme", "../..")


class TestSaveTokenUmask:
    """audit C16：写令牌临时文件前要先收紧 umask（Linux 上短暂 0644）。"""

    def test_sets_restrictive_umask_around_write(self, monkeypatch, tmp_path: Path) -> None:
        target = tmp_path / ".tok"
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", target)
        seen: list[int] = []
        real = os.umask

        def spy(mask: int) -> int:
            seen.append(mask)
            return real(mask)

        monkeypatch.setattr(gh.os, "umask", spy)
        gh.save_token("secret-token-value")

        assert seen and seen[0] == 0o077, f"写前未收紧 umask：{seen}"
        assert target.read_text(encoding="utf-8") == "secret-token-value"
