"""端到端演示：真实站点 → 抓取 → 落库。

用法：

    ./.tools/python/python.exe examples/demo_crawl.py            # 真实站点
    ./.tools/python/python.exe examples/demo_crawl.py --offline  # 离线自检（不联网）

演示 M2 抓取层的完整链路：HttpFetcher（限流/重试/UA）→ StaticHtmlCrawler
（声明式选择器）→ crawl_company 用例（幂等 upsert）→ SQLite。

**规格直接取自站点注册表**（`slices/crawl/sites.py` 的 `yingjiesheng`），不在这里
再抄一份。本地副本会随站点改版悄悄腐化 —— 实测这份示例的
`div.enterprise-list-item` / `a` 与注册表的 `.enterprise-list-item` /
`.enterprise-list-title` 早已对不上，而它恰好是唯一没进离线冒烟的示例：
没人跑，也就没人发现。现在规格只有一处定义，示例跟着注册表一起被测试覆盖。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from hunter1.platform.db import Database
from hunter1.platform.fetch.http import FetchError, HttpFetcher
from hunter1.slices.crawl import SITES, StaticHtmlCrawler, crawl_company

#: 演示用的站点 key —— 应届生首页企业列表（服务端渲染的静态列表，适合验证链路）。
TARGET_KEY = "yingjiesheng"
SITE = SITES[TARGET_KEY]
TARGET_URL = SITE.careers_url
SPEC = SITE.spec

# 离线自检用的内联页面：必须与**注册表里的选择器**对得上，否则这个自检会在
# 改版后变成「永远抓 0 条」。结构照 `tests/slices/crawl/fixtures/yingjiesheng.html` 写。
OFFLINE_HTML = """
<html><body>
  <div class="enterprise-list-item">
    <a href="/job/1"><span class="enterprise-list-title">某公司2027校园招聘</span></a>
  </div>
  <div class="enterprise-list-item">
    <a href="/job/2"><span class="enterprise-list-title">另一公司管培生计划</span></a>
  </div>
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
            company=f"{SITE.label}(离线)",
            careers_url=TARGET_URL,
            spec=SPEC,
            fetcher=_OfflineFetcher(),
        )
        result = crawl_company(crawler, jobs=db.jobs())
        fetcher = None
    else:
        print(f"[联网模式] 目标: {TARGET_URL}")
        fetcher = HttpFetcher(timeout=20, retries=2)
        crawler = StaticHtmlCrawler(
            company=SITE.label, careers_url=TARGET_URL, spec=SPEC, fetcher=fetcher
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
