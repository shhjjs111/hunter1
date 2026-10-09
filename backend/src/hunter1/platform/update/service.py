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
import zlib
from dataclasses import dataclass
from pathlib import Path

from hunter1.platform.update.client import DownloadError
from hunter1.platform.update.ports import ReleaseSource
from hunter1.platform.update.rules import (
    MAX_DOWNLOAD_BYTES,
    MAX_ENTRIES,
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
    - `ValueError`：没有可更新的版本、产物声明体积超限、包里有越界路径 / 解压后
      体积超限 / 条目数超限（包可疑，不「修正」）；
    - `DownloadError`：下载失败、**不是合法 zip**、压缩方法不受支持、成员被加密、
      **压缩数据损坏**、磁盘写入失败。

    这些形状原先全会裸穿 —— `BadZipFile` / `OSError` / `NotImplementedError` /
    `RuntimeError` / `zlib.error` 一个都不是 `ValueError`，于是 CLI 的
    `except (DownloadError, ValueError)` 兜不住，用户看到的是一段 traceback
    而不是「下载失败：…」。注意这是**开放集合**（`zlib.error` 的 MRO 直系
    `Exception`），所以解压阶段除了逐条认形状，还有一个 catch-all 兜底。
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
    except (DownloadError, ValueError):
        raise
    except OSError as exc:
        raise DownloadError("write_failed", str(exc)) from exc

    extracted = dest_dir / status.latest
    # 解压单独一个 try 块：这里的异常形状是**开放集合**（见下面的 catch-all），
    # 而上面下载/建目录的异常另有语义（写盘失败）—— 混在一起会让一个下载期的
    # TypeError 被说成「包坏了」。
    try:
        _extract_within(archive, extracted)
    except (DownloadError, ValueError):
        raise
    except zipfile.BadZipFile as exc:
        # 拿到的不是 zip（镜像返了一段 HTML / 传输被截断）—— FAIL 而不是 traceback
        raise DownloadError("archive_invalid", str(exc)) from exc
    except NotImplementedError as exc:
        # `zipfile` 对**不支持的压缩方法**（如 Deflate64=9）抛 NotImplementedError
        # （实测确认），它不是 `BadZipFile` 的子类 —— 原先裸穿成 traceback。
        raise DownloadError("archive_unsupported", str(exc)) from exc
    except RuntimeError as exc:
        # 加密成员 / 需要密码的 zip 抛 `RuntimeError("File ... is encrypted, password
        # required")`（同样是 `BadZipFile` 之外的形状）。归到「包不对劲」这一档。
        raise DownloadError("archive_encrypted", str(exc)) from exc
    except zlib.error as exc:
        # 中央目录**合法**、但压缩数据在解压中损坏（转存 / 重排 / 中间截断）。
        # `zlib.error` 的 MRO 是 `error → Exception`：ValueError / OSError /
        # RuntimeError / BadZipFile 一个都不是（实测），靶向 handler 全部捕不到。
        raise DownloadError("archive_corrupt", str(exc)) from exc
    except OSError as exc:
        raise DownloadError("write_failed", str(exc)) from exc
    except Exception as exc:
        # **catch-all 是有意的**：解压期能抛出的异常形状随 Python 版本与压缩后端
        # 变化（本文件已实测到四种：BadZipFile / NotImplementedError / RuntimeError
        # / zlib.error，其中后两种的 MRO 都与前一种无关）。逐个列举是打地鼠 ——
        # 漏掉一个就是裸穿 traceback，用户看到的不再是「下载失败：…」。
        # 这里只做「读 zip + 写文件」，任何未归类异常都只能来自这个包或解压后端，
        # 归到「包不对劲」是唯一有行动意义的结论；上面那些更精确的 handler 先接住
        # 常见形状，保持各自的错误码不变。
        raise DownloadError("archive_corrupt", str(exc)) from exc
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
    """拒绝体积超限 / 膨胀比异常 / 条目数异常的压缩包。

    用成员**声明**的解压尺寸求和：`zipfile` 按声明尺寸读取该成员（声明小了会被
    截断、不会多写），所以这个和就是解压写盘量的上界。声明得大 → 在这里就被挡。

    条目数也要单独挡 —— 两道体积闸门对它**都不生效**：每个成员都会引入文件系统
    条目（文件 + 目录项，本地头部几十到上百字节），于是一个体积远小于下载上限的包
    可以塞进数百万个 **0 字节**成员：`declared ≈ 0`（总量闸门不触发）、
    `declared/compressed` 比值也极低（膨胀比闸门不触发），但 `extractall` 会创建
    数百万个文件/目录，把磁盘 inode 与目录项耗尽。这与「几 KB 压成几 GB」是同类的
    写爆磁盘，只是换了维度。
    """
    if len(infos) > MAX_ENTRIES:
        raise ValueError(f"压缩包条目数超限，已拒绝：{len(infos)} > 上限 {MAX_ENTRIES}")
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
