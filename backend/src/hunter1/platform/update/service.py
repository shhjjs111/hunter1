"""自更新用例 —— 「有没有新版本、怎么拿到」。

只做决策与编排，不碰 HTTP：取清单与下载都走 `ReleaseSource` 端口，
因此「已最新 / 有新版 / 该版本没有本平台产物」这几条路径都能离线测。

**刻意不替换正在运行的程序。** Windows 上运行中的 exe 无法覆盖自己，绕过
这一点的通行做法（辅助进程 / 改名后重启）代价与风险都不小；而便携工具本来就
是「解压即用」，自然的更新动作就是「下好新版本 → 关掉程序 → 覆盖过去」。
所以这里把新版本解压到 `updates/<版本>/`，替换这一步交给用户确认。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from hunter1.platform.update.client import DownloadError
from hunter1.platform.update.ports import ProgressCallback, ReleaseSource
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


def prepare_update(
    *,
    source: ReleaseSource,
    status: UpdateStatus,
    dest_dir: Path,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """下载并解压到 `dest_dir/<版本>/`，返回解压目录。

    下载由端口负责（含 sha256 校验）；这里只管落位与解压，
    并且对压缩包做**越界路径检查** —— 宁可拒绝一个可疑包，也不要它写到目录外。

    `on_progress` 直通给 `source.download_asset`（契约本就带它，见
    `update.ports.ReleaseSource`）—— 此前这里从不传，于是 43MB 的包在 CLI 上是
    **静默等待**（报告次要项）。没有进度回调时行为不变。

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
    extracted = dest_dir / status.latest
    # 下载目录也可直接运行便携程序。数据目录、定制数据库或链接出现时，
    # 无法安全地把它当成纯构建产物替换，让用户换一个空下载目录。
    if extracted.exists() and any(
        path.is_symlink()
        or path.name.lower() in {"data", ".data", "access_token"}
        or path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
        for path in extracted.rglob("*")
    ):
        raise ValueError("目标版本目录包含用户数据或链接，已保留；请换一个空下载目录。")

    # 目录先建出来：磁盘预检与下载都需要它存在（原先这步在下面的 try 里与下载绑在
    # 一起，拆开是为了让预检能在**发起下载之前**给出结论）。
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError("write_failed", str(exc)) from exc

    # 清单里声明的体积先挡一道：比下载完再发现超限便宜，也让「这个包不对劲」
    # 在落盘之前就被说清楚。
    if status.asset.size is not None and status.asset.size > MAX_DOWNLOAD_BYTES:
        raise ValueError(
            f"产物声明体积超限，已拒绝：{status.asset.size} 字节 > 上限 {MAX_DOWNLOAD_BYTES}"
        )

    # 磁盘剩余空间预检（报告次要项）：下载 + 解压都要落盘。
    _ensure_free_space(dest_dir, status.asset.size)

    try:
        archive = dest_dir / f"hunter1-{status.latest}.zip"
        source.download_asset(status.asset, archive, on_progress=on_progress)
    except (DownloadError, ValueError):
        raise
    except OSError as exc:
        raise DownloadError("write_failed", str(exc)) from exc

    # 先在同一卷的独立目录解压；失败不能截断之前已准备好的版本。
    # 成功后先把旧目录移到备份，再安装完整新目录；安装失败则恢复旧目录。
    stage: Path | None = None
    ok = False
    try:
        stage = Path(tempfile.mkdtemp(prefix=".hunter1-update-", dir=dest_dir))
        unpacked = stage / "new"
        _extract_within(archive, unpacked)
        previous = stage / "previous"
        if extracted.exists():
            extracted.rename(previous)
        try:
            unpacked.rename(extracted)
        except OSError:
            if previous.exists():
                previous.rename(extracted)
            raise
        ok = True
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
    finally:
        if stage is not None:
            # 若恢复旧目录本身也失败，保留备份并明确给出位置，不能清掉用户唯一的旧版。
            previous = stage / "previous"
            if not ok and previous.exists():
                print(f"[update] 原版本保留在：{previous}", file=sys.stderr)
            else:
                _discard_partial(stage)
        if not ok:
            _discard_partial(archive)
    return extracted


def _ensure_free_space(dest_dir: Path, needed: int | None) -> None:
    """落盘前看一眼目标目录所在卷的剩余空间（报告次要项）。

    需要多少：下载的压缩包**加上**解压后的体积。解压后体积未知（上限见
    `MAX_EXTRACTED_BYTES`），所以用「压缩包的 2 倍」作**保守下界** —— 估得太高会
    拒绝一次本来能成功的更新（压缩率高的包实际占用远低于 2×），而真估低了也没关系：
    下面的写入路径仍会以 `write_failed` 优雅失败。

    `needed is None`（清单没声明体积）时跳过：那种情况下它多半是拿不到的，
    不如让写入路径去报真正的原因。
    """
    if needed is None:
        return
    try:
        free = shutil.disk_usage(dest_dir).free
    except OSError as exc:  # pragma: no cover - 取不到剩余空间就不拦
        print(f"[update] 无法读取磁盘剩余空间（{exc}），跳过预检。", file=sys.stderr)
        return
    if free < needed * 2:
        raise ValueError(
            f"磁盘剩余空间不足：约需 {needed * 2} 字节（压缩包 + 解压），可用 {free} 字节"
        )


def _discard_partial(target: Path) -> None:
    """清理本次临时目录或坏包；失败留痕，不覆盖更新的原始错误。"""
    try:
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    except OSError as exc:
        print(f"[update] 清理残留失败：{target}（{exc}）", file=sys.stderr)


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
        if not any(not info.is_dir() for info in infos):
            raise ValueError("更新压缩包为空，已拒绝")
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
