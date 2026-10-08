"""自更新用例 —— 「有没有新版本、怎么拿到」。

只做决策与编排，不碰 HTTP：取清单与下载都走 `ReleaseSource` 端口，
因此「已最新 / 有新版 / 该版本没有本平台产物」这几条路径都能离线测。

**刻意不替换正在运行的程序。** Windows 上运行中的 exe 无法覆盖自己，绕过
这一点的通行做法（辅助进程 / 改名后重启）代价与风险都不小；而便携工具本来就
是「解压即用」，自然的更新动作就是「下好新版本 → 关掉程序 → 覆盖过去」。
所以这里把新版本解压到 `updates/<版本>/`，替换这一步交给用户确认。
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from hunter1.platform.update.client import DownloadError
from hunter1.platform.update.ports import ReleaseSource
from hunter1.platform.update.rules import (
    MAX_DOWNLOAD_BYTES,
    MAX_EXPANSION_RATIO,
    MAX_EXTRACTED_BYTES,
    ReleaseAsset,
    is_newer,
)


@dataclass
class UpdateStatus:
    """一次「检查更新」的结论。"""

    current: str
    latest: str | None = None
    available: bool = False
    asset: ReleaseAsset | None = None
    notes: str | None = None
    detail: str = ""


def check_for_update(
    *,
    source: ReleaseSource,
    url: str,
    current_version: str,
    platform: str,
) -> UpdateStatus:
    """取清单并判断是否值得更新。

    三种「不更新」的情形要能分辨：远程更旧、版本相同、**该版本没有本平台的产物**。
    第三种最容易被含糊过去 —— 它的正确反应是明说，而不是退回另一个平台的包。
    """
    manifest = source.fetch_manifest(url)
    asset = manifest.asset_for(platform)

    if asset is None:
        return UpdateStatus(
            current=current_version,
            latest=manifest.version,
            detail=f"{manifest.version} 没有 {platform} 的产物，跳过",
        )
    if not is_newer(manifest.version, current_version):
        return UpdateStatus(
            current=current_version,
            latest=manifest.version,
            detail=f"已是最新（{current_version}）",
        )
    return UpdateStatus(
        current=current_version,
        latest=manifest.version,
        available=True,
        asset=asset,
        notes=manifest.notes,
        detail=f"可更新：{current_version} → {manifest.version}",
    )


def prepare_update(*, source: ReleaseSource, status: UpdateStatus, dest_dir: Path) -> Path:
    """下载并解压到 `dest_dir/<版本>/`，返回解压目录。

    下载由端口负责（含 sha256 校验）；这里只管落位与解压，
    并且对压缩包做**越界路径检查** —— 宁可拒绝一个可疑包，也不要它写到目录外。

    失败词汇只有两种，且都是调用方（CLI）已经认识的：
    - `ValueError`：没有可更新的版本、产物声明体积超限、包里有越界路径或解压后
      体积超限（包可疑，不「修正」）；
    - `DownloadError`：下载失败、**不是合法 zip**、磁盘写入失败。

    后两类原先裸穿（`zipfile.BadZipFile` 与 `OSError` 都不是 `ValueError`），
    于是 CLI 的 `except (DownloadError, ValueError)` 兜不住，用户看到的是一段
    traceback 而不是「下载失败：…」。
    """
    if not status.available or status.asset is None or status.latest is None:
        raise ValueError("没有可用的更新")

    # 清单里声明的体积先挡一道：比下载完再发现超限便宜，也让「这个包不对劲」
    # 在落盘之前就被说清楚。
    if status.asset.size is not None and status.asset.size > MAX_DOWNLOAD_BYTES:
        raise ValueError(
            f"产物声明体积超限，已拒绝：{status.asset.size} 字节 > 上限 {MAX_DOWNLOAD_BYTES}"
        )

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        archive = dest_dir / f"hunter1-{status.latest}.zip"
        source.download_asset(status.asset, archive)

        extracted = dest_dir / status.latest
        _extract_within(archive, extracted)
    except (DownloadError, ValueError):
        raise
    except zipfile.BadZipFile as exc:
        # 拿到的不是 zip（镜像返了一段 HTML / 传输被截断）—— FAIL 而不是 traceback
        raise DownloadError("archive_invalid", str(exc)) from exc
    except OSError as exc:
        raise DownloadError("write_failed", str(exc)) from exc
    return extracted


def _extract_within(archive: Path, dest: Path) -> None:
    """解压，但拒绝任何会写到 `dest` 之外的成员，并挡下「解压炸弹」。

    Python 的 `extract` 会静默剥掉 `..` 与绝对路径；这里选择**拒绝而不是修正**：
    一个正常的发布包不该包含这类路径，出现了就说明包本身可疑。

    总量闸门同样在 `extractall` **之前**做：`extractall` 自己没有体积上限，
    而清单未签名 —— 一个 5KB 的包可以解出几个 GB。
    """
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        for info in infos:
            if _escapes(info.filename, dest_resolved):
                raise ValueError(f"压缩包里存在越界路径，已拒绝：{info.filename}")
        _guard_expansion(archive, infos)
        bundle.extractall(dest)


def _guard_expansion(archive: Path, infos: list[zipfile.ZipInfo]) -> None:
    """拒绝体积超限 / 膨胀比异常的压缩包。

    用成员**声明**的解压尺寸求和：`zipfile` 按声明尺寸读取该成员（声明小了会被
    截断、不会多写），所以这个和就是解压写盘量的上界。声明得大 → 在这里就被挡。
    """
    declared = sum(info.file_size for info in infos)
    if declared > MAX_EXTRACTED_BYTES:
        raise ValueError(
            f"压缩包解压后体积超限，已拒绝：声明 {declared} 字节 > 上限 {MAX_EXTRACTED_BYTES}"
        )
    compressed = archive.stat().st_size
    if compressed > 0 and declared > compressed * MAX_EXPANSION_RATIO:
        raise ValueError(
            f"压缩包膨胀比超限，已拒绝：{declared} / {compressed} > {MAX_EXPANSION_RATIO}"
        )


def _escapes(name: str, dest: Path) -> bool:
    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        return True
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if ".." in parts:
        return True
    return not (dest / cleaned).resolve().is_relative_to(dest)


__all__ = ["UpdateStatus", "check_for_update", "prepare_update"]
