"""刷新站点快照（fixtures）—— 站点改版后重新生成离线回归样本。

用法：

    ./.tools/python/python.exe scripts/refresh_fixtures.py            # 全部站点
    ./.tools/python/python.exe scripts/refresh_fixtures.py zhipin     # 指定站点

做三件事：
1. 按 `slices.crawl` 站点表的选择器从**真实页面**抓取；
2. 裁剪成小快照（保留前 N 条 + 其祖先链 + 其后代，去掉脚本/样式/其余条目）；
3. 用同一套选择器回读裁剪结果，确认快照仍然可解析 —— 不合格就**不落盘**。

为什么裁剪：真实列表页动辄 0.5–1MB，整个塞进仓库既臃肿又难 diff；
而只保留前 N 条时，选择器仍然面对**真实的 DOM 结构**，改版照样能测出来。

快照是「选择器还认得真实页面」的证据，因此**只在回读通过时才写**：
宁可保留旧快照（并让 `test_site_registry` 继续用旧的过），也不要写入一个
已经被裁坏的样本 —— 那会让测试变成假绿。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from bs4 import BeautifulSoup

from hunter1.platform.fetch.http import HttpFetcher
from hunter1.slices.crawl import SITES, available_sites, ensure_not_blocked, parse_list_page

ROOT = Path(__file__).resolve().parent.parent
KEEP = 3
FIXTURES = ROOT / "backend" / "tests" / "slices" / "crawl" / "fixtures"


def prune(html: str, item_selector: str) -> str:
    """保留前 KEEP 个条目及其祖先/后代，其余节点全部删除。"""
    soup = BeautifulSoup(html, "lxml")
    items = soup.select(item_selector)
    if len(items) < KEEP:
        raise ValueError(f"页面里只找到 {len(items)} 条，少于 {KEEP}")

    keep: set[int] = set()
    for item in items[:KEEP]:
        keep.add(id(item))
        keep.update(id(anc) for anc in item.parents)
        keep.update(id(desc) for desc in item.find_all(True))

    # 用 id() 而不是 Tag 本身：bs4 的 Tag.__eq__ 是结构比较、__hash__ 是身份哈希，
    # 放进 set 做成员判断会漏（相同结构的两条会互相顶掉）。
    for element in soup.find_all(True):
        if id(element) in keep or element.parent is None:
            continue
        element.decompose()
    return str(soup)


def refresh(key: str, http: HttpFetcher) -> tuple[int, int]:
    site = SITES[key]
    html = http.get_text(site.careers_url)
    # 被风控拦下时给出真实原因（而不是「解析出 0 条」这种误导性的说法）
    ensure_not_blocked(html, url=site.careers_url)
    pruned = prune(html, site.spec.item_selector)

    jobs = parse_list_page(pruned, site.spec, company=site.label, page_url=site.careers_url)
    if len(jobs) < KEEP:
        raise ValueError(f"裁剪后只能解析出 {len(jobs)} 条，拒绝落盘")

    path = FIXTURES / f"{key}.html"
    before = path.stat().st_size if path.exists() else 0
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path.write_text(pruned, encoding="utf-8")
    return before, path.stat().st_size


def main(argv: list[str]) -> int:
    keys = argv or available_sites()
    unknown = [k for k in keys if k not in SITES]
    if unknown:
        print(f"未知站点：{unknown}；可用：{available_sites()}")
        return 2

    failed: list[str] = []
    with HttpFetcher(timeout=20, retries=2) as http:
        for key in keys:
            try:
                before, after = refresh(key, http)
                delta = f"{before} → {after}" if before else f"新建 {after}"
                print(f"[OK  ] {key:<16} {delta} 字节")
            except Exception as exc:  # 站点改版/风控属预期失败，逐站报告即可
                failed.append(key)
                print(f"[FAIL] {key:<16} {type(exc).__name__}: {exc}")

    if failed:
        print(f"\n{failed} 刷新失败；旧快照保留，请人工核对选择器。")
        return 1
    print("\n全部刷新完成；跑 `pytest tests/slices/crawl` 复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
