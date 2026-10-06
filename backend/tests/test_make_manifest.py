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
    path = tmp_path / "hunter1-win32.zip"
    path.write_bytes(b"fake zip payload" * 100)
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
            version="0.1.0",
            assets=[(platform, artifact)],
            url_for=lambda _p, name: f"https://example.com/v0.1.0/{name}",
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
        assert self._manifest(artifact).version == "0.1.0"

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
