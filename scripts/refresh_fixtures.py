"""刷新站点快照（fixtures）—— 站点改版后重新生成离线回归样本。

用法：

    ./.tools/python/python.exe scripts/refresh_fixtures.py            # 全部站点
    ./.tools/python/python.exe scripts/refresh_fixtures.py job910     # 指定站点

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

from hunter1.platform.console import enable_utf8_console
from hunter1.platform.fetch.http import HttpFetcher
from hunter1.slices.crawl import SITES, available_sites, ensure_not_blocked, parse_list_page

# 本脚本 print 中文（抓取进度/状态）—— 非中文 locale 控制台（cp1252）下会抛
# UnicodeEncodeError。共用同一份防护。
enable_utf8_console()

ROOT = Path(__file__).resolve().parent.parent
KEEP = 3
FIXTURES = ROOT / "backend" / "tests" / "slices" / "crawl" / "fixtures"


def prune(html: str, item_selector: str, count: int = KEEP) -> str:
    """保留前 `count` 个条目及其祖先/后代，其余节点全部删除。"""
    soup = BeautifulSoup(html, "lxml")
    items = soup.select(item_selector)
    if len(items) < count:
        raise ValueError(f"页面里只找到 {len(items)} 条，少于 {count}")

    keep: set[int] = set()
    for item in items[:count]:
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


def refresh_detail(key: str, http: HttpFetcher, detail_url: str) -> tuple[int, int]:
    """抓一个详情页并裁成快照（保留 JD 正文容器 + 其祖先链）。

    快照名与列表页区分开：`<key>_detail.html`。列表页快照是「列表选择器还认得
    真实页面」的证据，这一份是「**详情选择器**还认得真实页面」的证据 ——
    站点改版时它会先红，比用户发现「JD 全空了」早得多。

    裁剪保留**全部**命中容器（`count` 传该选择器的命中数），不是只留第一个：
    实测高校人才网一个详情页有 4 个 `.detail-item`（基本信息 / 其他要求 /
    岗位职责 / 任职要求），只留第一个会让快照只能解析出 76 字而非 1068 字 ——
    快照与实际抓取行为不符，回归用例就守不住「多容器拼接」这条语义。
    保留祖先链也是必要的：正文容器常靠父级 class 定位。
    """
    site = SITES[key]
    selector = site.spec.detail_selector
    assert selector is not None, f"{key} 没声明 detail_selector"  # noqa: S101
    headers = {"Referer": site.spec.detail_referer} if site.spec.detail_referer else None
    html = http.get_text(detail_url, headers=headers)
    ensure_not_blocked(html, url=detail_url)

    # 有多少个命中容器就留多少个 —— 与 `parse_detail_page` 的「拼接全部」对齐。
    hit_count = len(BeautifulSoup(html, "lxml").select(selector))
    if hit_count == 0:
        raise ValueError(f"页面里找不到 {selector!r} —— 详情选择器可能已失效")
    pruned = prune(html, selector, count=hit_count)

    path = FIXTURES / f"{key}_detail.html"
    before = path.stat().st_size if path.exists() else 0
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path.write_text(pruned, encoding="utf-8", newline="\n")
    return before, path.stat().st_size


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
    # newline="\n"：产物行尾不该由平台决定（与 export_openapi.py / make_manifest.py /
    # build.py 同一约定）。快照会入库，Windows 上默认写 CRLF 会让工作树与 git blob
    # 长期不一致（.gitattributes 是 eol=lf），且这份快照不在 test_artifact_line_endings
    # 的守卫清单里 —— 漏了 newline 就没有任何一层会发现。
    path.write_text(pruned, encoding="utf-8", newline="\n")
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
                continue

            # 详情页快照：只对**声明了 detail_selector 且列表快照刷新成功**的站做。
            # 详情页抓不到不算「刷新失败」——它在生产里也只是让该条没 JD
            # （见 `crawl_company` 的 per-job 隔离），不该拦住列表快照的成果。
            if SITES[key].spec.detail_selector is None:
                continue
            try:
                jobs = parse_list_page(
                    (FIXTURES / f"{key}.html").read_text(encoding="utf-8"),
                    SITES[key].spec,
                    company=SITES[key].label,
                    page_url=SITES[key].careers_url,
                )
                if not jobs:
                    print(f"[SKIP] {key:<16} 列表快照解析不出岗位，跳过详情页快照")
                    continue
                before, after = refresh_detail(key, http, jobs[0].detail_url)
                delta = f"{before} → {after}" if before else f"新建 {after}"
                print(f"[OK  ] {key:<16} 详情页 {delta} 字节")
            except Exception as exc:
                print(f"[WARN] {key:<16} 详情页快照未更新：{type(exc).__name__}: {exc}")

    if failed:
        print(f"\n{failed} 刷新失败；旧快照保留，请人工核对选择器。")
        return 1
    print("\n全部刷新完成；跑 `pytest tests/slices/crawl` 复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
