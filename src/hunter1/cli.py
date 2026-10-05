"""命令行入口。

    hunter1 serve [--db 路径] [--host] [--port] [--sites a,b] [--no-browser]
    hunter1 crawl [--db 路径] [--sites a,b]
    hunter1 update [--source URL] [--download]

`serve` 起本地 Web UI；`crawl` 不开界面直接跑一轮抓取（给定时任务用）；
`update` 检查并下载新版本。

默认数据库位置由 `paths` 决定：开发时在仓库的 `.data/`，打包后在程序目录
旁边的 `data/` —— 便携工具不该让用户去 `%APPDATA%` 里找库文件。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from hunter1 import __version__
from hunter1.crawlers.registry import available_sites
from hunter1.paths import data_dir, default_db_path
from hunter1.web.context import AppContext

# 更新源没有内建默认值：这个项目还没有发布渠道，编一个假 URL 只会让
# `hunter1 update` 发一次必然失败的请求。没配就明说没配。
UPDATE_SOURCE_ENV = "HUNTER1_UPDATE_SOURCE"


def _site_keys(raw: str) -> list[str] | None:
    """`--sites a,b` → ["a", "b"]；留空表示全部注册站点。"""
    keys = [item.strip() for item in (raw or "").split(",") if item.strip()]
    if not keys:
        return None
    unknown = [key for key in keys if key not in available_sites()]
    if unknown:
        raise SystemExit(f"未知站点 {unknown}；可用：{', '.join(available_sites())}")
    return keys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter1", description="Hunter1 求职工作台")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动本地 Web UI")
    serve.add_argument("--db", default=str(default_db_path()), help="SQLite 路径")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--sites", default="", help="逗号分隔的站点 key（默认全部）")
    serve.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")

    crawl = sub.add_parser("crawl", help="跑一轮抓取（不开界面）")
    crawl.add_argument("--db", default=str(default_db_path()))
    crawl.add_argument("--sites", default="")

    update = sub.add_parser("update", help="检查并下载新版本")
    update.add_argument(
        "--source",
        default=os.environ.get(UPDATE_SOURCE_ENV, ""),
        help=f"版本清单 URL（也可设环境变量 {UPDATE_SOURCE_ENV}）",
    )
    update.add_argument(
        "--download", action="store_true", help="有新版本时下载到本机（默认只检查）"
    )
    update.add_argument("--dest", default="", help="下载目标目录（默认 data/updates）")

    return parser


def _serve(args: argparse.Namespace) -> int:
    import threading
    import webbrowser

    import uvicorn

    from hunter1.web.app import create_app

    db_path = Path(args.db)
    # 先看库在不在，再让 AppContext 去建 —— 建过之后这个判断就失效了
    first_run = not db_path.exists()

    context = AppContext.default(db_path=db_path, site_keys=_site_keys(args.sites))
    app = create_app(context)
    url = f"http://{args.host}:{args.port}"

    print(f"Hunter1 已启动：{url}")
    print(f"数据库：{db_path}")
    if first_run:
        print(
            "首次运行：库已建好。先到「配置」页填 base_url / 模型 / API Key，再去「抓取」页跑一轮。"
        )

    if args.no_browser:
        pass
    elif first_run:
        # 首次运行自动开浏览器（用户还不知道要手点网址）；之后不再打扰
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    else:
        print("（加 --no-browser 可关闭自动打开浏览器；已配置过则不会自动打开）")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _crawl(args: argparse.Namespace) -> int:
    from hunter1.application.crawl import crawl_all

    context = AppContext.default(db_path=args.db, site_keys=_site_keys(args.sites))
    batch = crawl_all(context.crawler_factory(), jobs=context.db.jobs())
    for result in batch.results:
        flag = "OK  " if result.ok else "FAIL"
        print(
            f"[{flag}] {result.company:<12} 抓到 {result.fetched:>4}  "
            f"新增 {result.created:>4} 更新 {result.updated:>4}"
        )
        if result.error:
            print(f"         {result.error}")
    print(f"合计：抓到 {batch.fetched}，新增 {batch.created}，失败站点 {len(batch.failures)}")
    context.db.dispose()
    return 0 if batch.fetched else 1


def _update(args: argparse.Namespace) -> int:
    """检查更新；`--download` 时把新版本解压到本机。

    刻意**不自动替换正在运行的程序** —— Windows 上运行中的 exe 覆盖不了自己，
    绕开需要辅助进程那一套。这里做的是「下好并校验，你来覆盖」，与便携软件
    「解压即用」的更新动作一致。
    """
    from hunter1.application.update import check_for_update, prepare_update
    from hunter1.infrastructure.update import DownloadError, ReleaseClient

    if not args.source:
        print(f"未配置更新源。用 --source 指定版本清单 URL，或设置环境变量 {UPDATE_SOURCE_ENV}。")
        return 2

    dest = Path(args.dest) if args.dest else data_dir() / "updates"
    with ReleaseClient() as source:
        try:
            status = check_for_update(
                source=source,
                url=args.source,
                current_version=__version__,
                platform=sys.platform,
            )
        except DownloadError as exc:
            print(f"检查更新失败：{exc}")
            return 1

        print(status.detail)
        if status.notes:
            print(f"更新说明：{status.notes}")
        if not status.available:
            return 0
        if not args.download:
            print("（加 --download 可下载到本机）")
            return 0

        try:
            extracted = prepare_update(source=source, status=status, dest_dir=dest)
        except (DownloadError, ValueError) as exc:
            print(f"下载失败：{exc}")
            return 1

    print(f"已下载并解压到：{extracted}")
    print("关闭本程序后，用该目录里的内容覆盖程序目录即可完成更新。")
    return 0


def _enable_utf8_console(*streams: object) -> None:
    """让 Windows 控制台也能正确显示中文。

    控制台默认按本地代码页（简中 Windows 上是 CP936）编码；而 Git Bash、
    Windows Terminal、VS Code 终端都按 UTF-8 解码 —— 结果是用户第一次运行
    看到的提示就是一串乱码。这里把输出流固定到 UTF-8 并请控制台配合。

    只在 Windows 上动控制台代码页；非 Windows 直接返回。任何一步失败都
    静默跳过 —— 显示不好是小事，不能因此让程序起不来。
    """
    targets = streams or (sys.stdout, sys.stderr)

    if os.name == "nt":
        with contextlib.suppress(Exception):
            # 拿不到控制台时会抛（比如输出被重定向到文件）
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    for stream in targets:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _enable_utf8_console()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "crawl":
        return _crawl(args)
    if args.command == "update":
        return _update(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
