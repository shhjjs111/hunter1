"""自更新用例测试 —— 假来源 + 假包，全程离线。

覆盖三条决策路径（已最新 / 有新版 / 该版本没有本平台产物），
外加一件容易被忽略的事：**压缩包里的越界路径要拒绝而不是静默修正**。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hunter1.platform.update import (
    DownloadError,
    ReleaseAsset,
    ReleaseManifest,
    check_for_update,
    prepare_update,
)

SHA = "a" * 64


def _asset(platform: str = "win32", url: str = "https://example.com/w.zip") -> ReleaseAsset:
    return ReleaseAsset(platform=platform, url=url, sha256=SHA)


def _manifest(version: str, *, platforms: tuple[str, ...] = ("win32",), notes: str | None = None):
    return ReleaseManifest(
        version=version,
        notes=notes,
        assets=[_asset(p) for p in platforms],
    )


class FakeSource:
    """假来源：记录被请求的 URL，返回预置清单；下载就是写一个预置 zip。"""

    def __init__(
        self,
        *,
        manifest: ReleaseManifest | None = None,
        payload: bytes | None = None,
        fail_download: Exception | None = None,
        extract: str = "hunter1/hunter1.exe",
    ) -> None:
        self.manifest = manifest
        self.fail_download = fail_download
        self.requested: list[str] = []
        self.downloaded_to: list[Path] = []
        self.payload = payload if payload is not None else _zip_bytes(extract)

    def fetch_manifest(self, url: str) -> ReleaseManifest:
        self.requested.append(url)
        assert self.manifest is not None, "测试没给清单"
        return self.manifest

    def download_asset(
        self,
        asset: ReleaseAsset,
        dest: Path,
        *,
        on_progress: Any = None,
    ) -> Path:
        if self.fail_download is not None:
            raise self.fail_download
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.payload)
        self.downloaded_to.append(dest)
        return dest


def _zip_bytes(*names: str) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name in names:
            bundle.writestr(name, b"binary")
    return buffer.getvalue()


class TestCheckForUpdate:
    def test_reports_newer_version(self) -> None:
        source = FakeSource(manifest=_manifest("0.2.0", notes="修了几个 bug"))
        status = check_for_update(
            source=source,
            url="https://example.com/manifest.json",
            current_version="0.0.1",
            platform="win32",
        )
        assert status.available is True
        assert status.latest == "0.2.0"
        assert status.asset is not None
        assert status.notes == "修了几个 bug"
        assert "0.0.1" in status.detail and "0.2.0" in status.detail

    def test_same_version_is_not_an_update(self) -> None:
        source = FakeSource(manifest=_manifest("0.0.1"))
        status = check_for_update(
            source=source,
            url="https://example.com/manifest.json",
            current_version="0.0.1",
            platform="win32",
        )
        assert status.available is False
        assert "最新" in status.detail

    def test_older_remote_version_is_not_an_update(self) -> None:
        source = FakeSource(manifest=_manifest("0.0.1"))
        status = check_for_update(
            source=source,
            url="u",
            current_version="0.2.0",
            platform="win32",
        )
        assert status.available is False

    def test_missing_platform_asset_is_reported_not_guessed(self) -> None:
        """没有本平台产物时要说清，而不是退回别的平台的包。"""
        source = FakeSource(manifest=_manifest("0.2.0", platforms=("linux",)))
        status = check_for_update(source=source, url="u", current_version="0.0.1", platform="win32")
        assert status.available is False
        assert status.asset is None
        assert "win32" in status.detail

    def test_fetches_from_given_url(self) -> None:
        source = FakeSource(manifest=_manifest("0.2.0"))
        check_for_update(
            source=source,
            url="https://mirror.example.com/m.json",
            current_version="0.0.1",
            platform="win32",
        )
        assert source.requested == ["https://mirror.example.com/m.json"]


class TestPrepareUpdate:
    def _status(self, source: FakeSource):  # type: ignore[no-untyped-def]
        return check_for_update(source=source, url="u", current_version="0.0.1", platform="win32")

    def test_downloads_and_extracts(self, tmp_path: Path) -> None:
        source = FakeSource(manifest=_manifest("0.2.0"))
        extracted = prepare_update(
            source=source, status=self._status(source), dest_dir=tmp_path / "updates"
        )
        assert extracted == tmp_path / "updates" / "0.2.0"
        assert (extracted / "hunter1" / "hunter1.exe").is_file()

    def test_archive_is_kept_alongside(self, tmp_path: Path) -> None:
        source = FakeSource(manifest=_manifest("0.2.0"))
        extracted = prepare_update(
            source=source, status=self._status(source), dest_dir=tmp_path / "updates"
        )
        assert extracted.parent.joinpath("hunter1-0.2.0.zip").is_file()

    def test_refuses_when_nothing_available(self, tmp_path: Path) -> None:
        source = FakeSource(manifest=_manifest("0.0.1"))
        with pytest.raises(ValueError):
            prepare_update(
                source=source, status=self._status(source), dest_dir=tmp_path / "updates"
            )

    def test_download_failure_propagates(self, tmp_path: Path) -> None:
        source = FakeSource(
            manifest=_manifest("0.2.0"), fail_download=DownloadError("checksum_mismatch")
        )
        with pytest.raises(DownloadError):
            prepare_update(
                source=source, status=self._status(source), dest_dir=tmp_path / "updates"
            )

    def test_rejects_zip_with_escaping_path(self, tmp_path: Path) -> None:
        """包里有越界路径就拒绝 —— 这类包本身就可疑，不该静默「修正」它。"""
        source = FakeSource(manifest=_manifest("0.2.0"), payload=_zip_bytes("../evil.txt"))
        with pytest.raises(ValueError) as excinfo:
            prepare_update(
                source=source, status=self._status(source), dest_dir=tmp_path / "updates"
            )
        assert "evil.txt" in str(excinfo.value)
        assert not (tmp_path / "evil.txt").exists()

    def test_rejects_absolute_path_in_zip(self, tmp_path: Path) -> None:
        source = FakeSource(manifest=_manifest("0.2.0"), payload=_zip_bytes("/abs/evil.txt"))
        with pytest.raises(ValueError):
            prepare_update(
                source=source, status=self._status(source), dest_dir=tmp_path / "updates"
            )


def test_status_is_a_plain_dataclass() -> None:
    """状态对象要能直接打印/序列化，方便 CLI 与将来的界面复用。"""
    from dataclasses import asdict, is_dataclass

    from hunter1.platform.update import UpdateStatus

    assert is_dataclass(UpdateStatus)
    payload: dict[str, Any] = asdict(
        UpdateStatus(current="0.0.1", latest="0.2.0", available=True, detail="x")
    )
    assert payload["current"] == "0.0.1"


class TestManifestVersionIsNotAPath:
    """`version` 会被当成**路径成分**（`dest_dir / latest`、`hunter1-{latest}.zip`）。

    伪造一份清单让 version 带上跳（如 `9.9.9/../../evil`），解压就会写到目标
    目录之外。压缩包**成员**已有越界检查（见 TestPrepareUpdate），但目录名本身
    这条路径此前无人守 —— 校验器只查了非空。

    兼容红线：`v` 前缀与 `-beta`/`+local` 后缀是 `is_newer` 的既有语义，不能砍。
    """

    @pytest.mark.parametrize(
        "bad",
        [
            r"9.9.9\..\..\..\evil",  # 反斜杠分隔（Windows 风格）
            "1.2.3/../../x",
            "../evil",
            "..",
            "a/b",
            r"C:\Windows\Temp\evil",  # 盘符 + 反斜杠
            "c:/windows/temp/evil",  # 盘符 + 正斜杠
        ],
    )
    def test_rejects_path_like_versions(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            ReleaseManifest(version=bad, assets=[])

    def test_rejects_via_json_too(self) -> None:
        """真实攻击面是**下载来的 JSON**（fetch_manifest 走 model_validate_json）。"""
        payload = json.dumps({"version": "9.9.9/../../evil", "assets": []})
        with pytest.raises(ValidationError):
            ReleaseManifest.model_validate_json(payload)

    @pytest.mark.parametrize(
        "good",
        ["1.2.3", "v1.2.3", "1.2", "1.2.3-beta.1", "1.2.3+local", "0.0.1"],
    )
    def test_keeps_existing_version_forms(self, good: str) -> None:
        assert ReleaseManifest(version=good, assets=[]).version == good
