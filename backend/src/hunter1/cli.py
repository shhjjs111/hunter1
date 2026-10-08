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
import threading
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from hunter1 import __version__
from hunter1.main import AppContext
from hunter1.paths import data_dir, default_db_path
from hunter1.platform.db import DatabaseLocationError
from hunter1.slices.crawl import available_sites

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


def _onboarding_note(*, is_new_db: bool, configured: bool) -> str | None:
    """需要引导时返回提示文案，否则 None。

    判据是**「用户能不能用」**，不是「库文件在不在」。只看文件存在与否会漏掉
    最常见的困境：库建过了、也抓过几次，但一直没配 API Key —— 此时
    `db_path.exists()` 为真，引导全部失效，而用户此刻最需要知道的正是
    「先去配置页」。
    """
    if is_new_db:
        return (
            "首次运行：库已建好。先到「配置」页填 base_url / 模型 / API Key，再去「抓取」页跑一轮。"
        )
    if not configured:
        return "尚未配置模型：先到「配置」页填 base_url / 模型 / API Key，助手与评分才能用。"
    return None


def _open_browser_later(url: str, *, delay: float = 1.2) -> threading.Timer:
    """过一小会儿打开浏览器（让服务先起来）。

    `daemon=True` 是必须的：非守护线程会让解释器在退出时 join 它，
    Ctrl+C 之后还要干等一个 delay 才真正退出。
    """
    timer = threading.Timer(delay, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()
    return timer


def _is_llm_configured(context: AppContext) -> bool:
    """库里是否已有可用的模型配置。

    配置损坏时**不静默**：打印一句警告并当作未配置，让用户知道要重新保存，
    而不是看到一个「已配置」的假象或一次启动崩溃。
    """
    try:
        settings = context.db.settings().get_llm()
    except ValueError as exc:
        print(f"警告：已保存的模型配置读不出来（{exc}）。请到「配置」页重新保存一次。")
        return False
    return settings is not None and settings.is_configured


def _port_is_free(host: str, port: int) -> bool:
    """能否在 (host, port) 上绑定 —— 宣布「已启动」、开浏览器之前先探一次。

    这是**预检**（bind 一次即关），真正的绑定仍由 uvicorn 完成。要解决的是最常见
    的那种失败：端口被占时先打印一句「已启动」，还开一个指向死服务的浏览器页，
    用户得自己从 uvicorn 的报错里反推发生了什么。

    预检与真正绑定之间仍有一次 TOCTOU 窗口 —— 但那是毫秒级，且真撞上时 uvicorn
    自己会报错退出，不会留下错误的状态说明。
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from hunter1.main import create_app

    db_path = Path(args.db)
    # 必须在建库之前判断 —— Database 一初始化，这个信息就没了
    is_new_db = not db_path.exists()

    context = AppContext.default(db_path=db_path, site_keys=_site_keys(args.sites))
    app = create_app(context)
    url = f"http://{args.host}:{args.port}"

    if not _port_is_free(args.host, args.port):
        print(f"端口 {args.port} 已被占用：换一个 --port，或先停掉占用它的进程。")
        context.db.dispose()
        return 2

    print(f"Hunter1 已启动：{url}")
    print(f"数据库：{db_path}")

    note = _onboarding_note(is_new_db=is_new_db, configured=_is_llm_configured(context))
    if note is not None:
        print(note)
        # 需要引导时才自动开浏览器（用户还不知道要手点网址）；
        # 已经配好的人不该被反复弹浏览器。
        if not args.no_browser:
            _open_browser_later(url)
            print("（已尝试打开浏览器；加 --no-browser 可关闭）")

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        # `uvicorn.run` 是常驻的：用户按 Ctrl+C 抛的是 KeyboardInterrupt，
        # 正常返回路径根本走不到。所以清理必须在 finally —— 否则 SQLite 的
        # `-wal` / `-shm` 会留在数据目录里（实测可复现），与「删目录即卸载」
        # 的便携定位不符，也和 `_crawl` 的行为不一致。
        context.db.dispose()
    return 0


def _crawl(args: argparse.Namespace) -> int:
    from hunter1.slices.crawl import crawl_all

    context = AppContext.default(db_path=args.db, site_keys=_site_keys(args.sites))
    try:
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
    finally:
        # 与 `_serve` 同一约定：异常路径也必须释放连接 —— 否则 SQLite 的
        # `-wal` / `-shm` 会留在数据目录里（「删目录即卸载」的便携定位）。
        context.db.dispose()
    # 退出码按「站点是否全部成功」判，不按「抓到几条」：
    #   - 全部站点成功但本轮没有新岗位（都抓过了，很常见）→ 0，不是失败；
    #   - 有站点失败 → 1，哪怕别的站点抓到了几百条 —— 失败要让定时任务/CI 看见。
    return 0 if batch.ok else 1


def _update(args: argparse.Namespace) -> int:
    """检查更新；`--download` 时把新版本解压到本机。

    刻意**不自动替换正在运行的程序** —— Windows 上运行中的 exe 覆盖不了自己，
    绕开需要辅助进程那一套。这里做的是「下好并校验，你来覆盖」，与便携软件
    「解压即用」的更新动作一致。
    """
    from hunter1.platform.update import (
        DownloadError,
        ReleaseClient,
        check_for_update,
        prepare_update,
    )

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
        except ValueError as exc:
            # 清单里 version 无法解析（`is_newer` → `parse_version` 抛的裸 ValueError，
            # 如 version="release-x"）。与 `fetch_manifest` 的解析失败对齐成同一个
            # 可判别的形态 —— 否则用户看到的是一段 traceback。
            print(f"检查更新失败：{DownloadError('manifest_invalid', str(exc))}")
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
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command == "crawl":
            return _crawl(args)
        if args.command == "update":
            return _update(args)
    except DatabaseLocationError as exc:
        # 环境问题，不是程序缺陷：给一句人话 + 一个可行动的提示，
        # 而不是把 traceback 砸到用户脸上。
        print(f"数据库位置不可用：{exc}")
        print("换一个可写的位置（--db ./hunter1.db），或检查该文件是否被占用/损坏。")
        return 2
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
