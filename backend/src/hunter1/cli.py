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
import os
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path

from sqlalchemy.exc import DatabaseError

from hunter1 import __version__
from hunter1.main import AppContext
from hunter1.paths import data_dir, default_db_path
from hunter1.platform.console import enable_utf8_console
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
    # 版本号只有一个来源（包内的 `__version__`，见 pyproject.toml 的注释）——
    # 打包/更新链都从它派生，所以自查版本必须走同一条路。
    parser.add_argument("--version", action="version", version=f"hunter1 {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动本地 Web UI（不带参数直接运行 = 这个）")
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


def _parse_args(argv: Sequence[str] | None) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """解析命令行；**不带子命令时默认走 `serve`**。

    便携交付形态下，用户拿到的就是一个 `hunter1.exe`，默认动作是**双击**——
    而双击不带任何参数。此前那种情况会 `print_help()` 然后 exit 1：一个黑框
    一闪而过，用户以为程序坏了（实测：双击后只看到黑框，界面从不出现）。

    这与打包冒烟形成一个盲区闭环：冒烟测的是 `hunter1.exe serve`（显式带子命令），
    所以「无参数」这条**用户最常走的路**从未被任何测试覆盖。

    默认值直接从 `serve` 子解析器取（`parse_args([])`），不另抄一份 —— 否则
    `--db` 的默认路径、端口、host 会在两处各写一遍，迟早漂移。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # 借用 serve 子解析器自己的默认值，保证与 `hunter1 serve` 完全一致。
        serve_defaults = parser.parse_args(["serve"])
        args = serve_defaults
    return parser, args


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


def _port_problem(host: str, port: int) -> str | None:
    """能否在 (host, port) 上绑定 —— 宣布「已启动」、开浏览器之前先探一次。

    这是**预检**（bind 一次即关），真正的绑定仍由 uvicorn 完成。要解决的是最常见
    的那种失败：端口被占时先打印一句「已启动」，还开一个指向死服务的浏览器页，
    用户得自己从 uvicorn 的报错里反推发生了什么。

    返回 `None` 表示可用；否则返回一句**给人看的原因**。

    ⚠ 必须区分「被别人占着」与「本进程无权绑定」：Linux 上非 root 绑 <1024 的端口
    会抛 `PermissionError`（同样是 `OSError`），把它一律说成「端口已被占用」会让人
    去杀一个根本不存在的进程（CI 上就是这么暴露的：测试用 `--port 1`，Windows 上
    能绑、Linux 上不行）。

    预检与真正绑定之间仍有一次 TOCTOU 窗口 —— 但那是毫秒级，且真撞上时 uvicorn
    自己会报错退出，不会留下错误的状态说明。
    """
    import errno
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except PermissionError:
            return f"端口 {port} 无权绑定（1024 以下是特权端口，需要管理员/root 权限）。"
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM):
                return f"端口 {port} 无权绑定（权限不足）。"
            return f"端口 {port} 已被占用：换一个 --port，或先停掉占用它的进程。"
    return None


def _release_context(context: AppContext) -> None:
    """释放组装处持有的资源。

    只 `db.dispose()` 会漏掉 `HttpFetcher`（httpx 连接池）—— 连接不关就随进程
    生命周期累积，与 `examples/demo_crawl.py` 的 `fetcher.close()`、
    `refresh_fixtures` 的 `with HttpFetcher(...)` 口径不一致。

    `TextFetcher` 端口只声明了 `get_text`、没有 `close`，所以用 `getattr` 取：
    真正的 `HttpFetcher.close()` 会被调到，测试里的无状态假件（没有 close）干净跳过。
    """
    context.db.dispose()
    closer = getattr(context.fetcher, "close", None)
    if closer is not None:
        closer()


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from hunter1.main import create_app

    db_path = Path(args.db)
    # 必须在建库之前判断 —— Database 一初始化，这个信息就没了
    is_new_db = not db_path.exists()

    context = AppContext.default(db_path=db_path, site_keys=_site_keys(args.sites))
    app = create_app(context)
    url = f"http://{args.host}:{args.port}"

    port_problem = _port_problem(args.host, args.port)
    if port_problem is not None:
        print(port_problem)
        _release_context(context)
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
        _release_context(context)
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
        _release_context(context)
    # 退出码按「站点是否全部成功」判，不按「抓到几条」：
    #   - 全部站点成功但本轮没有新岗位（都抓过了，很常见）→ 0，不是失败；
    #   - 有站点失败 → 1，哪怕别的站点抓到了几百条 —— 失败要让定时任务/CI 看见。
    return 0 if batch.ok else 1


def _progress_reporter(expected: int | None) -> Callable[[int, int | None], None]:
    """下载进度回调 —— 几十 MB 的包不该在 CLI 上静默等待（报告次要项）。

    单行刷新（`\\r` + 不换行的 `print`）：包会按 MB 分块回调几十次，逐行打印会刷屏。
    主体输出前由调用方补一个换行（见 `_update` 里的 `print(f"\\n已下载…")`）。
    """

    def report(received: int, total: int | None) -> None:
        span = total or expected
        if span:
            percent = min(100, received * 100 // span)
            print(f"\r下载中 {percent}%（{received // 1024} KB）…", end="", flush=True)
        else:
            print(f"\r已下载 {received // 1024} KB…", end="", flush=True)

    return report


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
            extracted = prepare_update(
                source=source,
                status=status,
                dest_dir=dest,
                on_progress=_progress_reporter(
                    status.asset.size if status.asset is not None else None
                ),
            )
        except (DownloadError, ValueError) as exc:
            # 前导换行：进度用的是单行刷新（`\r`），不换行会把错误顶掉一半。
            print(f"\n下载失败：{exc}")
            return 1

    print(f"\n已下载并解压到：{extracted}")
    print("关闭本程序后，用该目录里的内容覆盖程序目录即可完成更新。")
    return 0


def _enable_utf8_console(*streams: object) -> None:
    """让 Windows 控制台也能正确显示中文（委托 `platform.console`，一处定义）。

    控制台默认按本地代码页编码（简中 CP936 能编码中文，但英文 Windows —— 含 GitHub
    的 windows runner —— 是 **cp1252，编不了任何中文**）。本函数的实现现已抽到
    `hunter1.platform.console.enable_utf8_console`，示例与 CLI 共用同一份 ——
    此前每个入口各抄一份，`examples/` 里有三个就因此漏掉了防护、在 cp1252 的 CI
    上一 print 中文就崩。

    保留本名（而非直接改调用点为公开名）是为了不打断既有测试的导入
    （`tests/test_cli.py` 直接 import `_enable_utf8_console`）。
    """
    enable_utf8_console(*streams)


def main(argv: Sequence[str] | None = None) -> int:
    _enable_utf8_console()
    # 不带子命令（= 双击 exe）**默认进 serve**，见 `_parse_args`。
    _parser, args = _parse_args(argv)
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
    except DatabaseError as exc:
        # 连得上、只是结构不成立（补列撞名 / 建索引失败 / schema 漂移）。
        # **换路径修不好它** —— 照搬上面那两句会把用户引向错误的方向，
        # 所以这里刻意给不同的话术。
        print(f"数据库初始化失败（结构性错误，换 --db 路径没有用）：{exc}")
        print("这更像实现或数据迁移缺陷；请把上面这行原文附在问题里。")
        return 2
    # 三个子命令都在上面分派掉了，走到这里是**实现缺陷**（新增子命令忘了接线）。
    # 原先这里 print_help + return 1 —— 那是「无参数」走的路径，现在无参数已归
    # 到 serve，所以这里只剩「新子命令没接」这种情况。大声说出来，别伪装成帮助。
    print(f"内部错误：子命令 {args.command!r} 没有对应的处理函数。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
