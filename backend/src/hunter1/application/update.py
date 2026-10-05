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

from hunter1.application.ports import ReleaseSource
from hunter1.domain.update import ReleaseAsset, is_newer


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
    """
    if not status.available or status.asset is None or status.latest is None:
        raise ValueError("没有可用的更新")

    dest_dir.mkdir(parents=True, exist_ok=True)
    archive = dest_dir / f"hunter1-{status.latest}.zip"
    source.download_asset(status.asset, archive)

    extracted = dest_dir / status.latest
    _extract_within(archive, extracted)
    return extracted


def _extract_within(archive: Path, dest: Path) -> None:
    """解压，但拒绝任何会写到 `dest` 之外的成员。

    Python 的 `extract` 会静默剥掉 `..` 与绝对路径；这里选择**拒绝而不是修正**：
    一个正常的发布包不该包含这类路径，出现了就说明包本身可疑。
    """
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if _escapes(name, dest_resolved):
                raise ValueError(f"压缩包里存在越界路径，已拒绝：{name}")
        bundle.extractall(dest)


def _escapes(name: str, dest: Path) -> bool:
    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        return True
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if ".." in parts:
        return True
    return not (dest / cleaned).resolve().is_relative_to(dest)


__all__ = ["UpdateStatus", "check_for_update", "prepare_update"]
