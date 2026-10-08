"""生成自更新清单（`manifest.json`）—— 发布流程的一步。

    ./.tools/python/python.exe scripts/make_manifest.py \
        --asset win32=dist/hunter1-win32.zip \
        --url-base https://github.com/OWNER/REPO/releases/download/v0.1.0 \
        --out dist/manifest.json

三条不能错的约定（都有测试钉住）：

1. **`version` 取自包本身**（`hunter1.__version__`），不是命令行传进来的 ——
   清单版本与二进制版本不一致时，`is_newer` 的比较会长期失准而**不报任何错**。
   `--version` 只用于「断言一致」，不一致就拒绝生成。
2. **产物必须自带同一版本**：zip 里要有 `hunter1/VERSION` 且与源码一致，否则拒绝
   生成 —— 一份忘了重建的旧 `dist/` 会让清单写新版本、包里装旧程序（见
   `asset_version_problem`）。
3. **`platform` 用 `sys.platform` 词汇**（`win32` / `darwin` / `linux`）。
   `asset_for()` 是精确匹配，写 `windows` 不会报错、只会永远匹配不上 ——
   即「有产物却永远收不到更新」。已知的错词直接拒绝，不留给发布日去发现。
4. **产物必须真算 sha256**（分块读，大包不吃内存）—— 清单里 `sha256` 缺失或
   格式不对，`ReleaseManifest` 解析就会拒；生成端先算对，别把问题推给用户端。

最后一步用 `ReleaseManifest` 回读自己产出的 JSON：**生成物必须能被消费端的
模型接受**，否则清单发出去也没人能用。
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 脚本在仓库根、包在 backend/src —— 显式加路径，不依赖安装状态
# （本机解释器是 Python embeddable，不走 PYTHONPATH），所以下面的 import 必须
# 晚于这一行 —— 不是「忘了放顶部」。
sys.path.insert(0, str(ROOT / "backend" / "src"))

from hunter1 import __version__  # noqa: E402
from hunter1.platform.update import ReleaseManifest, file_sha256  # noqa: E402

#: 与更新链的 `asset_for()` 输入对齐 —— 这份词汇表是 `sys.platform` 的值。
PLATFORM_VOCAB = ("win32", "darwin", "linux")

#: 产物 zip 的布局（`scripts/build.py` 打的包）：单一根目录 + 根下 `VERSION`。
#: 这两个常量必须与 build.py 的 `APP_NAME` / `VERSION_FILE_NAME` 一致 ——
#: 由 tests/test_make_manifest.py 断言（各留一份字面量会让布局悄悄漂移，
#: 而漂移的表现是「发布时核对不到版本」，正是这道闸要防的事）。
_ZIP_ROOT = "hunter1"
_VERSION_ENTRY = "VERSION"

#: 容易写错、且错了不报错的词。`paths.py` 用的是另一套（windows/macos），
#: 混淆两者的后果是「有产物但永远匹配不上」。在这里挡住，而非留给发布日。
_PLATFORM_TRAPS = {
    "windows": "win32",
    "macos": "darwin",
    "mac": "darwin",
    "osx": "darwin",
    "win64": "win32",
    "linux2": "linux",
}


class ManifestError(RuntimeError):
    """生成清单失败（输入不合法）。"""


#: URL 前缀里出现这些词，几乎一定是没替换的模板占位符。清单本身是合法的
#: （http 地址 + 正确 sha256），所以**不会报任何错** —— 只会让所有用户的
#: 更新请求打到一个不存在的地址。发布前必须替换。
_PLACEHOLDER_TOKENS = ("OWNER", "REPO", "CHANGE-ME", "CHANGEME", "TODO", "YOUR-ORG")


def warn_if_placeholder(url_base: str) -> str | None:
    """URL 前缀像模板占位符时返回提示语，否则 None。

    **大小写敏感**：占位符的惯例是全大写（`OWNER` / `REPO`）。不能先把 URL
    转成大写再匹配 —— 那会让真实仓库名里的 `repo`（如 `my-repo`）命中占位符
    词，给所有这类仓库发假告警（实测踩到过：`example/repo` 被判成没替换）。
    """
    text = url_base or ""
    hit = [token for token in _PLACEHOLDER_TOKENS if token in text]
    if not hit:
        return None
    return (
        f"url-base 里还有占位符 {hit}：{url_base}\n"
        "  清单本身合法（地址是 http、sha256 正确），所以不会报错 —— 但所有用户的"
        "更新请求会打到一个不存在的地址。上传后请用真实地址重新生成。"
    )


def parse_asset_spec(spec: str) -> tuple[str, Path]:
    """把 `[platform=]path` 解析成 (platform, 路径)。

    省略 platform 时用当前解释器的 `sys.platform` —— 你就是在为当前平台构建
    产物，这是最不容易错的默认值。
    """
    raw = (spec or "").strip()
    if not raw:
        raise ManifestError("--asset 不能为空")
    platform, sep, path_text = raw.partition("=")
    if not sep:
        return sys.platform, Path(raw)
    platform = platform.strip()
    if not platform:
        raise ManifestError(f"--asset 的平台名为空：{spec!r}")
    return platform, Path(path_text.strip())


def _check_platform(platform: str) -> str:
    cleaned = (platform or "").strip()
    if not cleaned:
        raise ManifestError("平台名不能为空")
    if cleaned in _PLATFORM_TRAPS:
        raise ManifestError(
            f"平台名 {cleaned!r} 是更新链词汇表之外的值（那是 paths.py 的词汇）。"
            f"清单必须用 sys.platform 词汇：{' / '.join(PLATFORM_VOCAB)} —— "
            f"应为 {_PLATFORM_TRAPS[cleaned]!r}。写错不会报错，只会永远匹配不上。"
        )
    if cleaned not in PLATFORM_VOCAB:
        # 未知值不直接拒绝（可能是别的平台），但说出来，避免默默发出去。
        print(
            f"提示：平台名 {cleaned!r} 不在已知词汇 {' / '.join(PLATFORM_VOCAB)} 里，"
            f"请确认它与 update 端的 sys.platform 值一致。",
            file=sys.stderr,
        )
    return cleaned


def asset_version_problem(path: Path) -> str | None:
    """核对 zip 产物自带的 `VERSION` 与源码版本。合格返回 None。

    为什么必须核对：清单里的 `version` 取自**源码**（`hunter1.__version__`），而二进制
    是构建时烧进去的。若手边是一份**上次**构建、忘了重建的 `dist/`，清单会写上新版本
    号、包里却是旧程序 —— 用户看到「有新版本」，装回去的是旧的，`is_newer` 从此长期
    失准且不报任何错。构建端与发布端各自都对，只有中间那个文件是旧的。

    判据按「产物类型」分开：zip 必须带 VERSION 且一致（缺了就拒绝，否则这道闸形同
    不存在）；其它形态（tar.gz 等）读不出，提示一句「无法核对」但不拦。
    """
    if path.suffix.lower() != ".zip":
        print(
            f"提示：{path.name} 不是 zip，无法核对产物↔源码版本（这是本项目的发行形态？）",
            file=sys.stderr,
        )
        return None

    entry = f"{_ZIP_ROOT}/{_VERSION_ENTRY}"
    try:
        with zipfile.ZipFile(path) as bundle:
            names = set(bundle.namelist())
            if entry not in names:
                return (
                    f"产物 {path.name} 里没有 {entry} —— 无法核对「这份二进制是不是 "
                    f"{__version__} 构建的」。请用 scripts/build.py --zip 重新打包。"
                )
            found = bundle.read(entry).decode("utf-8", "replace").strip()
    except (OSError, zipfile.BadZipFile) as exc:
        return f"产物 {path.name} 打不开：{type(exc).__name__}: {exc}"

    if found != __version__:
        return (
            f"产物版本 {found!r} 与源码 {__version__!r} 不一致 —— 这是一份旧构建"
            "（清单写新版本、包里是旧程序，用户的更新判断会长期失准）。"
        )
    return None


def build_manifest(
    *,
    version: str,
    assets: Sequence[tuple[str, Path]],
    url_for: Callable[[str, str], str],
    notes: str | None = None,
) -> ReleaseManifest:
    """组装并**回读验证**清单。

    `url_for(platform, filename)` 给出每个产物的绝对下载地址 —— 上传后的真实
    地址只有调用方知道（GitHub Release 是
    `…/releases/download/v0.1.0/<文件名>`）。
    """
    if not assets:
        raise ManifestError("至少要有一个产物（--asset）")

    entries: list[dict[str, object]] = []
    for platform, path in assets:
        canonical = _check_platform(platform)
        if not path.is_file():
            raise ManifestError(f"产物不存在：{path}")
        # 产物自带版本与源码不一致时**拒绝生成**：清单发出去之后，用户端只能看到
        # 「版本号」这一个信号，装错版本没有任何办法被发现。
        version_problem = asset_version_problem(path)
        if version_problem is not None:
            raise ManifestError(version_problem)
        entries.append(
            {
                "platform": canonical,
                "url": url_for(canonical, path.name),
                "sha256": file_sha256(path),
                "size": path.stat().st_size,
            }
        )

    payload: dict[str, object] = {"version": version, "assets": entries}
    if notes:
        payload["notes"] = notes

    # 回读：生成物必须能被消费端模型接受。字段形状、url 协议、sha256 格式
    # 都在这一句里被真正校验一次（而不是等用户端拒绝）。
    return ReleaseManifest.model_validate(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make_manifest.py", description="生成自更新清单")
    parser.add_argument(
        "--asset",
        action="append",
        default=[],
        metavar="[platform=]PATH",
        help="产物文件；省略 platform 时用当前 sys.platform。可重复",
    )
    parser.add_argument(
        "--url-base",
        required=True,
        help="下载地址前缀（如 …/releases/download/v0.1.0），产物名会拼在其后",
    )
    parser.add_argument("--out", default=str(ROOT / "dist" / "manifest.json"), help="输出路径")
    parser.add_argument("--version", default="", help=f"断言与此值一致（默认取包内 {__version__}）")
    parser.add_argument("--notes", default="", help="版本说明（可选）")
    args = parser.parse_args(argv)

    if args.version and args.version.strip() != __version__:
        print(
            f"版本不一致：命令行给的是 {args.version!r}，包内是 {__version__!r}。"
            "清单版本与二进制版本不一致时，更新判断会长期失准且不报错 —— 拒绝生成。",
            file=sys.stderr,
        )
        return 2

    try:
        assets = [parse_asset_spec(spec) for spec in args.asset]
        base = args.url_base.rstrip("/")
        manifest = build_manifest(
            version=__version__,
            assets=assets,
            url_for=lambda _platform, filename: f"{base}/{filename}",
            notes=args.notes.strip() or None,
        )
    except ManifestError as exc:
        print(f"生成清单失败：{exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(manifest.model_dump(exclude_none=True), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",  # 见 export_openapi.py：产物不该由平台决定行尾
    )

    warning = warn_if_placeholder(args.url_base)
    if warning is not None:
        print(f"警告：{warning}", file=sys.stderr)

    print(f"== 清单已生成：{out} ==")
    print(f"  version：{manifest.version}")
    for asset in manifest.assets:
        size_mb = (asset.size or 0) / 1024 / 1024
        print(f"  {asset.platform:<8} {asset.url}  ({size_mb:.1f}MB, {asset.sha256[:12]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
