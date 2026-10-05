"""下载与校验 —— 自更新的 IO 半边。

两条硬约定：

1. **先下到 `.part`，校验通过才改名到目标**。这样「目标位置有文件」就等于
   「这个文件校验过了」。反过来（先写目标再校验）会让一次网络抖动留下一份
   看似完整的坏包。
2. **校验失败不留痕**：临时文件删掉，目标位置原样不动 —— 已有的一份好包
   不该被一次失败的下载毁掉。

摘要计算与下载**同流进行**：不把整个包读进内存，也不为了算 hash 再读一遍盘。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import httpx
from pydantic import ValidationError

from hunter1.platform.update.ports import ProgressCallback
from hunter1.platform.update.rules import ReleaseAsset, ReleaseManifest

_CHUNK = 1024 * 1024


class DownloadError(RuntimeError):
    """下载或校验最终失败。`code` 是稳定的机器可读标识。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}{': ' + message if message else ''}")
        self.code = code


def file_sha256(path: Path) -> str:
    """分块计算文件 sha256（大包也不会吃满内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download_verified(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    expected_sha256: str,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """流式下载到 `dest`，边下边校验 sha256；不一致则什么都不留。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    expected = (expected_sha256 or "").strip().lower()
    digest = hashlib.sha256()
    received = 0

    try:
        with client.stream("GET", url, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise DownloadError(f"http_{response.status_code}", url)
            total_header = response.headers.get("content-length")
            total = int(total_header) if (total_header or "").isdigit() else None
            with part.open("wb") as handle:
                for chunk in response.iter_bytes(_CHUNK):
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if on_progress is not None:
                        on_progress(received, total)
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise DownloadError("transport_failed", str(exc)) from exc
    except DownloadError:
        part.unlink(missing_ok=True)
        raise
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise DownloadError("write_failed", str(exc)) from exc

    if digest.hexdigest() != expected:
        part.unlink(missing_ok=True)  # 半个坏包比没有包更危险
        raise DownloadError("checksum_mismatch", url)

    # 校验通过才落到目标位置 —— 原子改名，避免出现"改到一半"的状态
    shutil.move(str(part), str(dest))
    return dest


class ReleaseClient:
    """按 URL 取版本清单、按产物下载文件。用 httpx，可注入 transport 以便离线测试。"""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=max(1.0, float(timeout)), transport=transport, follow_redirects=True
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ReleaseClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def fetch_manifest(self, url: str) -> ReleaseManifest:
        """取并解析版本清单。任何一步不对都抛 `DownloadError`。"""
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise DownloadError("transport_failed", str(exc)) from exc
        if response.status_code >= 400:
            raise DownloadError(f"http_{response.status_code}", url)
        try:
            return ReleaseManifest.model_validate_json(response.text)
        except ValidationError as exc:
            raise DownloadError("manifest_invalid", str(exc)) from exc

    def download_asset(
        self, asset: ReleaseAsset, dest: Path, *, on_progress: ProgressCallback | None = None
    ) -> Path:
        return download_verified(
            self._client,
            asset.url,
            dest,
            expected_sha256=asset.sha256,
            on_progress=on_progress,
        )


__all__ = ["DownloadError", "ReleaseClient", "download_verified", "file_sha256"]
