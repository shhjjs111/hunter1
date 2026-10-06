"""多站点抓取演示：注册表 → 真实站点 → 落库。

用法：

    ./.tools/python/python.exe examples/demo_sites.py             # 真实站点
    ./.tools/python/python.exe examples/demo_sites.py --offline   # 用快照，不联网

演示 M2 的完整链路：`crawlers.registry`（站点表）→ `HttpFetcher`
（主机限流 + 重试 + UA 轮换）→ `StaticHtmlCrawler`（声明式选择器）
→ `crawl_all`（批量 upsert，单站失败不中断）→ SQLite。

合规：只抓公开列表页，不做登录、不由程序投递；`max_pages` 已压低。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from hunter1.application.crawl import crawl_all
from hunter1.crawlers.registry import SITES, available_sites, build_all
from hunter1.platform.fetch.http import HttpFetcher
from hunter1.platform.db import Database

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "backend" / "tests" / "crawlers" / "fixtures"


class _FixtureFetcher:
    """离线模式：按站点 key 返回快照。"""

    def __init__(self, key: str) -> None:
        self.html = (FIXTURES / f"{key}.html").read_text(encoding="utf-8")

    def get_text(self, url: str, **_kw: object) -> str:
        return self.html


def main(argv: list[str]) -> int:
    offline = "--offline" in argv
    db_path = Path(tempfile.mkdtemp(prefix="hunter1-sites-")) / "jobs.db"
    db = Database(db_path)
    db.initialize()

    keys = available_sites()
    print(f"站点数：{len(keys)}  模式：{'离线快照' if offline else '真实网络'}")
    print(f"目标：{', '.join(f'{k}({SITES[k].label})' for k in keys)}\n")

    http = None
    if offline:
        crawlers = [_build_offline(key) for key in keys]
    else:
        http = HttpFetcher(timeout=20, retries=2)
        crawlers = build_all(fetcher=http)

    batch = crawl_all(crawlers, jobs=db.jobs())

    for result in batch.results:
        flag = "OK " if result.ok else "FAIL"
        print(
            f"[{flag}] {result.company:<10} 抓到 {result.fetched:>3}  "
            f"新增 {result.created:>3}  更新 {result.updated:>3}"
        )
        if result.error:
            print(f"       错误：{result.error}")

    print(
        f"\n合计：抓到 {batch.fetched}  新增 {batch.created}  更新 {batch.updated}  "
        f"失败站点 {len(batch.failures)}/{len(batch.results)}"
    )
    print(f"库中岗位：{db.jobs().count()}")

    print("\n--- 抽样（每站一条）---")
    seen: set[str] = set()
    for job in db.jobs().list(limit=200):
        if job.source in seen:
            continue
        seen.add(job.source)
        city = f" · {job.city}" if job.city else ""
        print(f"  [{job.source}] {job.title[:40]}{city}")

    db.dispose()
    if http is not None:
        http.close()

    if batch.fetched == 0:
        print("\n[FAIL] 一个岗位都没抓到")
        return 1
    print("\nPASS")
    return 0


def _build_offline(key: str):
    from hunter1.crawlers.registry import build_site

    return build_site(key, fetcher=_FixtureFetcher(key))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
