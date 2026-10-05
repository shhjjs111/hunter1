"""端到端演示：真实站点 → 抓取 → 落库。

用法：

    ./.tools/python/python.exe examples/demo_crawl.py            # 真实站点
    ./.tools/python/python.exe examples/demo_crawl.py --offline  # 离线自检（不联网）

演示 M2 抓取层的完整链路：HttpFetcher（限流/重试/UA）→ StaticHtmlCrawler
（声明式选择器）→ crawl_company 用例（幂等 upsert）→ SQLite。

注意：本演示用**应届生**的首页企业列表。它是服务端渲染的静态列表，
适合验证链路；但真实「逐站适配」通常还需要为每个站调整选择器与分页规则。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from hunter1.application.crawl import crawl_company
from hunter1.crawlers.static_html import ListPageSpec, StaticHtmlCrawler
from hunter1.infrastructure.crawler.http import FetchError, HttpFetcher
from hunter1.infrastructure.db import Database

TARGET_URL = "https://www.yingjiesheng.com/"
SPEC = ListPageSpec(
    url_template=TARGET_URL,
    item_selector="div.enterprise-list-item",
    title_selector="a",
    max_pages=1,
)

OFFLINE_HTML = """
<html><body>
  <div class="enterprise-list-item"><a href="/job/1">某公司2027校园招聘</a></div>
  <div class="enterprise-list-item"><a href="/job/2">另一公司管培生计划</a></div>
</body></html>
"""


class _OfflineFetcher:
    def get_text(self, url: str, **_kwargs: object) -> str:
        return OFFLINE_HTML


def main(argv: list[str]) -> int:
    offline = "--offline" in argv
    db_path = Path(tempfile.mkdtemp(prefix="hunter1-demo-")) / "jobs.db"
    db = Database(db_path)
    db.initialize()

    if offline:
        print("[离线模式] 使用内联 HTML，不联网")
        crawler: StaticHtmlCrawler = StaticHtmlCrawler(
            company="应届生(离线)", careers_url=TARGET_URL, spec=SPEC, fetcher=_OfflineFetcher()
        )
        result = crawl_company(crawler, jobs=db.jobs())
        fetcher = None
    else:
        print(f"[联网模式] 目标: {TARGET_URL}")
        fetcher = HttpFetcher(timeout=20, retries=2)
        crawler = StaticHtmlCrawler(
            company="应届生", careers_url=TARGET_URL, spec=SPEC, fetcher=fetcher
        )
        try:
            result = crawl_company(crawler, jobs=db.jobs())
        except FetchError as exc:
            print(f"[FAIL] 抓取失败: {exc.code} {exc.url}")
            return 1

    print(f"公司: {result.company}")
    print(f"抓到: {result.fetched}  新增: {result.created}  更新: {result.updated}")
    if result.error:
        print(f"错误: {result.error}")
        return 1

    jobs = db.jobs().list(limit=100)
    print(f"库中岗位: {db.jobs().count()}")
    print("--- 前 5 条 ---")
    for job in jobs[:5]:
        print(f"  [{job.capture_status.value}] {job.title[:44]}")
        print(f"       {job.detail_url[:88]}")
        print(f"       title_key={job.title_key[:30]}")

    db.dispose()
    if fetcher is not None:
        fetcher.close()

    if not jobs:
        print("[FAIL] 未抓到任何岗位")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
