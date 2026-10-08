"""清单生成脚本的测试。

盯三件「错了不报错、只在用户端才发作」的事：

1. **version 与二进制不一致** —— `is_newer` 的比较长期失准而无声；
2. **platform 用了 paths.py 的词汇**（`windows`）—— `asset_for` 精确匹配，
   永远匹配不上，表现为「有产物却收不到更新」；
3. **sha256 / size 与实际产物不符** —— 用户端校验失败，或更糟：校验了个寂寞。

最后一条是**回读**：生成的 JSON 必须能被 `ReleaseManifest` 接受，
否则清单发出去也没人能用。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module() -> ModuleType:
    """按路径加载脚本（scripts/ 不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location(
        "make_manifest_script", ROOT / "scripts" / "make_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make_manifest = _load_module()


@pytest.fixture()
def artifact(tmp_path: Path) -> Path:
    """一份**真的 zip 产物**（带 `hunter1/VERSION`）。

    以前这里写的是 `b"fake zip payload"` 这种假字节 —— 而清单生成现在会真的打开产物
    核对版本（见 `asset_version_problem`）：用假字节就测不到「版本一致时能生成」这条
    正常路径，只能测到「打不开」。真实的包结构才有区分度。
    """
    path = tmp_path / "hunter1-win32.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        # 版本取**包内真值**（不是硬编码 0.1.0）：版本号一升，这里跟着走，
        # 免得测试因为「升了版」而红 —— 那不是它要测的东西。
        bundle.writestr(
            f"{make_manifest._ZIP_ROOT}/{make_manifest._VERSION_ENTRY}",
            f"{make_manifest.__version__}\n",
        )
        bundle.writestr(f"{make_manifest._ZIP_ROOT}/hunter1.exe", "stub")
    return path


class TestParseAssetSpec:
    def test_explicit_platform(self) -> None:
        platform, path = make_manifest.parse_asset_spec("win32=dist/a.zip")
        assert platform == "win32"
        assert path == Path("dist/a.zip")

    def test_bare_path_defaults_to_current_platform(self) -> None:
        """省略平台时用 sys.platform —— 你就是在为当前平台构建。"""
        platform, path = make_manifest.parse_asset_spec("dist/a.zip")
        assert platform == sys.platform
        assert path == Path("dist/a.zip")

    def test_empty_spec_is_rejected(self) -> None:
        with pytest.raises(make_manifest.ManifestError):
            make_manifest.parse_asset_spec("   ")


class TestBuildManifest:
    def _manifest(self, artifact: Path, platform: str = "win32"):
        return make_manifest.build_manifest(
            version=make_manifest.__version__,
            assets=[(platform, artifact)],
            url_for=lambda _p, name: f"https://example.com/v{make_manifest.__version__}/{name}",
        )

    def test_computes_real_sha256_and_size(self, artifact: Path) -> None:
        import hashlib

        manifest = self._manifest(artifact)
        asset = manifest.assets[0]
        expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert asset.sha256 == expected
        assert asset.size == artifact.stat().st_size

    def test_url_is_absolute_and_http(self, artifact: Path) -> None:
        assert self._manifest(artifact).assets[0].url.startswith("https://example.com/")

    def test_version_is_recorded_verbatim(self, artifact: Path) -> None:
        assert self._manifest(artifact).version == make_manifest.__version__

    def test_asset_for_finds_the_entry(self, artifact: Path) -> None:
        """消费端拿它做精确匹配 —— 键值必须对得上。"""
        manifest = self._manifest(artifact)
        assert manifest.asset_for("win32") is not None
        assert manifest.asset_for("darwin") is None  # 不退回别的平台

    def test_paths_vocabulary_is_rejected_with_hint(self, artifact: Path) -> None:
        """`windows` 是 paths.py 的词汇 —— 写错不报错的正是这一类。"""
        with pytest.raises(make_manifest.ManifestError) as excinfo:
            self._manifest(artifact, platform="windows")
        message = str(excinfo.value)
        assert "win32" in message  # 指出正确值
        assert "sys.platform" in message

    def test_macos_trap_is_rejected(self, artifact: Path) -> None:
        with pytest.raises(make_manifest.ManifestError):
            self._manifest(artifact, platform="macos")

    def test_missing_artifact_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(make_manifest.ManifestError):
            self._manifest(tmp_path / "nope.zip")

    def test_no_assets_is_rejected(self) -> None:
        with pytest.raises(make_manifest.ManifestError):
            make_manifest.build_manifest(
                version="0.1.0", assets=[], url_for=lambda _p, n: f"https://x/{n}"
            )

    def test_can_be_reparsed_by_the_consumer_model(self, artifact: Path) -> None:
        """回读：生成物必须能被消费端的模型接受。"""
        from hunter1.platform.update import ReleaseManifest

        manifest = self._manifest(artifact)
        again = ReleaseManifest.model_validate(manifest.model_dump(exclude_none=True))
        assert again == manifest


class TestAssetPlatformMatchesItsFilename:
    """清单里的 `platform` 必须与产物名里的平台一致。

    两者不一致时清单**合法**（形状、sha256、url 全对，`ReleaseManifest` 照收），
    只在用户端发作：Windows 用户下到一个 Linux 包、Linux 用户永远看到「本平台没有
    产物」。实测 `release.sh` 曾把 `win32=` 写死，非 Windows 上必然踩中 —— 而它是
    发布文档指的那条路径。所以这里 fail-closed。
    """

    @staticmethod
    def _zip(tmp_path: Path, name: str) -> Path:
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr(
                f"{make_manifest._ZIP_ROOT}/{make_manifest._VERSION_ENTRY}",
                f"{make_manifest.__version__}\n",
            )
            bundle.writestr(f"{make_manifest._ZIP_ROOT}/hunter1.exe", "stub")
        return path

    def _build(self, path: Path, platform: str) -> object:
        return make_manifest.build_manifest(
            version=make_manifest.__version__,
            assets=[(platform, path)],
            url_for=lambda _p, name: f"https://example.com/{name}",
        )

    def test_matching_name_and_platform_passes(self, tmp_path: Path) -> None:
        manifest = self._build(self._zip(tmp_path, "hunter1-linux.zip"), "linux")
        assert manifest.assets[0].platform == "linux"  # type: ignore[attr-defined]

    def test_mismatch_is_rejected_and_names_both_sides(self, tmp_path: Path) -> None:
        path = self._zip(tmp_path, "hunter1-linux.zip")
        with pytest.raises(make_manifest.ManifestError) as excinfo:
            self._build(path, "win32")
        message = str(excinfo.value)
        assert "hunter1-linux.zip" in message and "win32" in message and "'linux'" in message

    def test_other_shapes_are_not_policed(self, tmp_path: Path) -> None:
        """非约定命名（tar.gz、手搓的包）不套这条规则 —— 它们本来就没有平台约定。"""
        path = tmp_path / "hunter1-linux.tar.gz"
        path.write_bytes(b"payload")
        manifest = self._build(path, "win32")
        assert manifest.assets[0].platform == "win32"  # type: ignore[attr-defined]


class TestAssetCarriesItsVersion:
    """产物↔源码版本：清单里的 version 取自源码，而二进制是构建时烧进去的。

    两者对不上时**没有任何人会发现**：清单写新版本、包里装旧程序，用户看到「有新版本」
    装回去还是旧的，`is_newer` 从此长期失准。所以发布端必须核对产物自带的 VERSION。
    """

    @staticmethod
    def _zip_with(tmp_path: Path, *, version: str | None) -> Path:
        path = tmp_path / f"hunter1-{sys.platform}.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            if version is not None:
                bundle.writestr(f"{make_manifest._ZIP_ROOT}/VERSION", version)
            bundle.writestr(f"{make_manifest._ZIP_ROOT}/hunter1.exe", "stub")
        return path

    def test_matching_version_passes(self, tmp_path: Path) -> None:
        path = self._zip_with(tmp_path, version=f"{make_manifest.__version__}\n")
        assert make_manifest.asset_version_problem(path) is None

    def test_mismatched_version_is_reported_with_both_values(self, tmp_path: Path) -> None:
        path = self._zip_with(tmp_path, version="0.0.1")
        problem = make_manifest.asset_version_problem(path)
        assert problem is not None
        assert "0.0.1" in problem and make_manifest.__version__ in problem

    def test_missing_version_entry_tells_you_to_rebuild(self, tmp_path: Path) -> None:
        path = self._zip_with(tmp_path, version=None)
        problem = make_manifest.asset_version_problem(path)
        assert problem is not None
        assert "build.py" in problem  # 给出可行动的下一步

    def test_unreadable_artifact_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "hunter1-win32.zip"
        path.write_bytes(b"not a zip at all")
        problem = make_manifest.asset_version_problem(path)
        assert problem is not None and "打不开" in problem

    def test_unversioned_asset_is_refused_by_build_manifest(self, tmp_path: Path) -> None:
        """走公开入口再确认一次：拒绝发生在生成清单这一步，而不是只在一个辅助函数里。"""
        path = self._zip_with(tmp_path, version="0.0.1")
        with pytest.raises(make_manifest.ManifestError):
            make_manifest.build_manifest(
                version=make_manifest.__version__,
                assets=[("win32", path)],
                url_for=lambda _p, name: f"https://example.com/{name}",
            )

    def test_non_zip_asset_is_allowed_but_flagged(self, tmp_path: Path, capsys) -> None:
        """其它形态（tar.gz 等）读不出 VERSION：不拦，但要说清「这道核对没做」。"""
        path = tmp_path / "hunter1-linux.tar.gz"
        path.write_bytes(b"payload")
        assert make_manifest.asset_version_problem(path) is None
        assert "无法核对" in capsys.readouterr().err

    def test_zip_layout_matches_what_build_py_produces(self) -> None:
        """两个脚本各留一份布局字面量，必须由断言钉住 —— 布局漂移的表现是
        「发布时核对不到版本」，正是这道闸要防的事。"""
        spec = importlib.util.spec_from_file_location(
            "build_script_for_layout", ROOT / "scripts" / "build.py"
        )
        assert spec is not None and spec.loader is not None
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)

        assert make_manifest._ZIP_ROOT == build.APP_NAME
        assert make_manifest._VERSION_ENTRY == build.VERSION_FILE_NAME


class TestCli:
    def test_writes_file_and_is_reparseable(self, artifact: Path, tmp_path: Path) -> None:
        out = tmp_path / "manifest.json"
        code = make_manifest.main(
            [
                "--asset",
                f"win32={artifact}",
                "--url-base",
                "https://example.com/v0.1.0/",
                "--out",
                str(out),
            ]
        )
        assert code == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["version"] == make_manifest.__version__
        assert payload["assets"][0]["platform"] == "win32"
        # 尾斜杠要被归一，不能拼出 `…/v0.1.0//x.zip`
        assert "//" not in payload["assets"][0]["url"].split("://")[1]

    def test_mismatched_version_is_refused(self, artifact: Path, tmp_path: Path) -> None:
        """清单版本与二进制版本不一致 = 更新判断长期失准且不报错 —— 拒绝生成。"""
        out = tmp_path / "manifest.json"
        code = make_manifest.main(
            [
                "--asset",
                f"win32={artifact}",
                "--url-base",
                "https://example.com/v0.1.0",
                "--out",
                str(out),
                "--version",
                "9.9.9",
            ]
        )
        assert code == 2
        assert not out.exists()

    def test_platform_trap_exits_nonzero(self, artifact: Path, tmp_path: Path) -> None:
        out = tmp_path / "manifest.json"
        code = make_manifest.main(
            [
                "--asset",
                f"windows={artifact}",
                "--url-base",
                "https://example.com/v0.1.0",
                "--out",
                str(out),
            ]
        )
        assert code == 2
        assert not out.exists()


class TestPlaceholderWarning:
    """清单合法（http + 正确 sha256）所以不报错 —— 但地址指向不存在的地方。"""

    def test_detects_owner_repo_placeholder(self) -> None:
        warning = make_manifest.warn_if_placeholder(
            "https://github.com/OWNER/REPO/releases/download/v0.1.0"
        )
        assert warning is not None
        assert "占位符" in warning

    def test_real_url_is_not_flagged(self) -> None:
        assert (
            make_manifest.warn_if_placeholder(
                "https://github.com/acme/hunter1/releases/download/v0.1.0"
            )
            is None
        )

    def test_url_containing_repo_word_is_not_flagged(self) -> None:
        """真实仓库名里含 `repo` 字样（如 my-repo）不能被误判成占位符。

        这条是**实测暴露**的：检测原先把整个 URL 转大写再匹配，于是
        `example/repo` 的 `repo` 大写后命中了占位符词 `REPO`，
        任何叫 `xxx-repo` 的仓库都会收到假告警。
        """
        for url in (
            "https://github.com/acme/my-repo/releases/download/v0.1.0",
            "https://github.com/owner/repo/releases/download/v0.1.0",
        ):
            assert make_manifest.warn_if_placeholder(url) is None, url

    def test_uppercase_placeholder_is_still_caught(self) -> None:
        """占位符的惯例是全大写 —— 真的没替换时仍要抓到。"""
        assert (
            make_manifest.warn_if_placeholder(
                "https://github.com/OWNER/REPO/releases/download/v0.1.0"
            )
            is not None
        )

    def test_cli_still_writes_file_but_warns(self, artifact: Path, tmp_path: Path, capsys) -> None:
        """告警不阻断生成（占位符清单仍有价值：sha256/size 是真的），
        但必须说出来，不能让人以为可以直接发。"""
        out = tmp_path / "manifest.json"
        code = make_manifest.main(
            [
                "--asset",
                f"win32={artifact}",
                "--url-base",
                "https://github.com/OWNER/REPO/releases/download/v0.1.0",
                "--out",
                str(out),
            ]
        )
        assert code == 0
        assert out.exists()
        assert "占位符" in capsys.readouterr().err
