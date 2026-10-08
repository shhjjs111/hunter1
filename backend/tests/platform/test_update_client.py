"""下载与校验的测试 —— 离线（httpx.MockTransport）。

这里盯着一条不变量：**校验失败绝不在目标位置留下文件**。
半个坏包比没有包更危险 —— 下一次「已经下好了」的判断会相信它。

两套客户端刻意分开：`download_verified` 只依赖裸 `httpx.Client`（它管传输），
`ReleaseClient` 是更高一层的封装（管清单与产物）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from hunter1.platform.update import (
    DownloadError,
    ReleaseAsset,
    ReleaseClient,
    download_verified,
    file_sha256,
)

PAYLOAD = b"hunter1-package-bytes" * 64
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _http(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _client(handler) -> ReleaseClient:  # type: ignore[no-untyped-def]
    return ReleaseClient(transport=httpx.MockTransport(handler))


def _blob(payload: bytes = PAYLOAD):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    return handler


class TestFileSha256:
    def test_matches_hashlib(self, tmp_path: Path) -> None:
        path = tmp_path / "blob.bin"
        path.write_bytes(PAYLOAD)
        assert file_sha256(path) == PAYLOAD_SHA

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.bin"
        path.write_bytes(b"")
        assert file_sha256(path) == hashlib.sha256(b"").hexdigest()


class TestDownloadVerified:
    def test_downloads_and_verifies(self, tmp_path: Path) -> None:
        with _http(_blob()) as client:
            target = download_verified(
                client,
                "https://example.com/a.zip",
                tmp_path / "a.zip",
                expected_sha256=PAYLOAD_SHA,
            )
        assert target.read_bytes() == PAYLOAD

    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deep" / "a.zip"
        with _http(_blob()) as client:
            download_verified(
                client, "https://example.com/a.zip", target, expected_sha256=PAYLOAD_SHA
            )
        assert target.is_file()

    def test_checksum_mismatch_leaves_nothing_behind(self, tmp_path: Path) -> None:
        target = tmp_path / "a.zip"
        with _http(_blob()) as client, pytest.raises(DownloadError) as excinfo:
            download_verified(client, "https://example.com/a.zip", target, expected_sha256="b" * 64)

        assert excinfo.value.code == "checksum_mismatch"
        assert not target.exists()  # 目标位置干净
        assert not list(tmp_path.glob("*.part"))  # 临时文件也清掉

    def test_existing_file_is_untouched_on_mismatch(self, tmp_path: Path) -> None:
        """已经有一份好包时，校验失败不该把它毁掉。"""
        target = tmp_path / "a.zip"
        target.write_bytes(b"previous-good-package")

        with _http(_blob()) as client, pytest.raises(DownloadError):
            download_verified(client, "https://example.com/a.zip", target, expected_sha256="b" * 64)
        assert target.read_bytes() == b"previous-good-package"

    def test_http_error_raises(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        target = tmp_path / "a.zip"
        with _http(handler) as client, pytest.raises(DownloadError) as excinfo:
            download_verified(
                client, "https://example.com/a.zip", target, expected_sha256=PAYLOAD_SHA
            )
        assert excinfo.value.code == "http_404"
        assert not target.exists()

    def test_transport_error_raises(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        with _http(handler) as client, pytest.raises(DownloadError) as excinfo:
            download_verified(
                client,
                "https://example.com/a.zip",
                tmp_path / "a.zip",
                expected_sha256=PAYLOAD_SHA,
            )
        assert excinfo.value.code == "transport_failed"

    def test_invalid_url_is_wrapped_not_leaked(self, tmp_path: Path) -> None:
        """清单里的 URL 非法 → DownloadError，而不是裸的 httpx.InvalidURL。

        `InvalidURL` 的 mro 是 `InvalidURL → Exception`（**不是** `HTTPError`），
        只 catch HTTPError 会让它穿过 CLI 的 `except (DownloadError, ValueError)`，
        用户看到的是一段 traceback。
        """
        with _http(_blob()) as client, pytest.raises(DownloadError) as excinfo:
            download_verified(
                client,
                "http://[::1:80/x.zip",  # 端口非法：httpx 构造请求时即拒绝
                tmp_path / "a.zip",
                expected_sha256=PAYLOAD_SHA,
            )
        assert excinfo.value.code == "invalid_url"
        assert not (tmp_path / "a.zip").exists()

    def test_unwritable_parent_directory_is_wrapped(self, tmp_path: Path) -> None:
        """父目录建不出来（被同名文件占着）→ DownloadError("write_failed")。

        建目录原在 try 之外，OSError 会裸穿 —— 与「失败一律 DownloadError」的
        承诺不符。
        """
        blocker = tmp_path / "afile"
        blocker.write_bytes(b"x")

        with _http(_blob()) as client, pytest.raises(DownloadError) as excinfo:
            download_verified(
                client,
                "https://example.com/a.zip",
                blocker / "a.zip",
                expected_sha256=PAYLOAD_SHA,
            )
        assert excinfo.value.code == "write_failed"

    def test_cleanup_failure_does_not_mask_the_real_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """清理半包时 `unlink` 自己抛错，**不许顶掉**正在报的那个错误。

        实测（Linux）：父路径是普通文件时 `unlink` 抛 `NotADirectoryError`，而
        `missing_ok=True` **不**吞它 —— 于是逃出去的是 `NotADirectoryError`，
        调用方（CLI 只认 DownloadError）拿到一个与「下载失败」毫无关系的异常。
        Windows 抛的是 `FileNotFoundError`（被吞），所以这个 bug 只在 Linux 可见 ——
        这条用例把 `unlink` 打进错误状态，在任何平台都能钉住它。
        """
        blocker = tmp_path / "afile"
        blocker.write_bytes(b"x")

        def denied(*_args: object, **_kwargs: object) -> None:
            raise NotADirectoryError(20, "Not a directory")

        monkeypatch.setattr(Path, "unlink", denied)

        with _http(_blob()) as client, pytest.raises(DownloadError) as excinfo:
            download_verified(
                client, "https://example.com/a.zip", blocker / "a.zip", expected_sha256=PAYLOAD_SHA
            )
        assert excinfo.value.code == "write_failed"

    def test_failed_final_move_is_wrapped_not_leaked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """校验通过后的改名也要是 DownloadError。

        改名（`shutil.move`）在下载的 try **之外** —— 目标被别的进程占着、
        跨设备、父路径是文件时它都会抛 `OSError`，不单独兜住就又是一次裸异常逃逸，
        而调用方的错误词汇表里只有 `DownloadError`。
        """
        import hunter1.platform.update.client as client_module

        def boom(*_args: object, **_kwargs: object) -> None:
            raise OSError(18, "Invalid cross-device link")

        monkeypatch.setattr(client_module.shutil, "move", boom)
        dest = tmp_path / "a.zip"

        with _http(_blob()) as client, pytest.raises(DownloadError) as excinfo:
            download_verified(
                client, "https://example.com/a.zip", dest, expected_sha256=PAYLOAD_SHA
            )

        assert excinfo.value.code == "write_failed"
        assert not dest.exists()
        assert not dest.with_name("a.zip.part").exists(), "改名失败也不能留半包"

    def test_overwrites_existing_file_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "a.zip"
        target.write_bytes(b"stale")

        with _http(_blob()) as client:
            download_verified(
                client, "https://example.com/a.zip", target, expected_sha256=PAYLOAD_SHA
            )
        assert target.read_bytes() == PAYLOAD

    def test_progress_callback_reports_bytes(self, tmp_path: Path) -> None:
        seen: list[tuple[int, int | None]] = []
        with _http(_blob()) as client:
            download_verified(
                client,
                "https://example.com/a.zip",
                tmp_path / "a.zip",
                expected_sha256=PAYLOAD_SHA,
                on_progress=lambda received, total: seen.append((received, total)),
            )
        assert seen and seen[-1][0] == len(PAYLOAD)


class TestFetchManifest:
    def test_parses_manifest(self) -> None:
        body = (
            '{"version": "0.3.0", "assets": [{"platform": "win32",'
            ' "url": "https://example.com/w.zip", "sha256": "' + "a" * 64 + '"}]}'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=body)

        with _client(handler) as client:
            manifest = client.fetch_manifest("https://example.com/manifest.json")
        assert manifest.version == "0.3.0"
        assert manifest.asset_for("win32") is not None

    def test_http_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        with _client(handler) as client, pytest.raises(DownloadError):
            client.fetch_manifest("https://example.com/manifest.json")

    def test_malformed_json_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")

        with _client(handler) as client, pytest.raises(DownloadError) as excinfo:
            client.fetch_manifest("https://example.com/manifest.json")
        assert excinfo.value.code == "manifest_invalid"

    def test_transport_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        with _client(handler) as client, pytest.raises(DownloadError) as excinfo:
            client.fetch_manifest("https://example.com/manifest.json")
        assert excinfo.value.code == "transport_failed"


class TestDownloadAsset:
    def test_uses_asset_url_and_checksum(self, tmp_path: Path) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, content=PAYLOAD)

        asset = ReleaseAsset(platform="win32", url="https://example.com/w.zip", sha256=PAYLOAD_SHA)
        with _client(handler) as client:
            target = client.download_asset(asset, tmp_path / "w.zip")
        assert seen["url"] == "https://example.com/w.zip"
        assert target.read_bytes() == PAYLOAD

    def test_checksum_failure_propagates(self, tmp_path: Path) -> None:
        asset = ReleaseAsset(platform="win32", url="https://example.com/w.zip", sha256="c" * 64)
        with _client(_blob()) as client, pytest.raises(DownloadError):
            client.download_asset(asset, tmp_path / "w.zip")
