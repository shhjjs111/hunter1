"""命令行入口。

    hunter1 serve [--db 路径] [--host] [--port] [--sites a,b]
    hunter1 crawl [--db 路径] [--sites a,b]

`serve` 起本地 Web UI；`crawl` 不开界面直接跑一轮抓取（给定时任务用）。
两条路都走同一套 `AppContext`，因此行为一致。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from hunter1.crawlers.registry import available_sites
from hunter1.web.context import AppContext

DEFAULT_DB = ".data/hunter1.db"


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
    serve.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 路径（默认 {DEFAULT_DB}）")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--sites", default="", help="逗号分隔的站点 key（默认全部）")

    crawl = sub.add_parser("crawl", help="跑一轮抓取（不开界面）")
    crawl.add_argument("--db", default=DEFAULT_DB)
    crawl.add_argument("--sites", default="")

    return parser


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from hunter1.web.app import create_app

    context = AppContext.default(db_path=args.db, site_keys=_site_keys(args.sites))
    app = create_app(context)
    print(f"Hunter1 已启动：http://{args.host}:{args.port}  （数据库 {Path(args.db)}）")
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "crawl":
        return _crawl(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
