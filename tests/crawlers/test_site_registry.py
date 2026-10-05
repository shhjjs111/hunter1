"""站点注册表的离线回归 —— 每个站点用真实页面快照跑一遍。

fixture 是**真实页面裁剪**（保留前 3 条 + 必要祖先链），所以这些测试
既能离线跑，又能守住「选择器还认得真实 DOM」这件事：站点改版时，
这里会红，而不是等到线上抓不到才被发现。

fixture 的生成/更新方式：`scripts/refresh_fixtures.py`（抓真实页面 → 裁剪 →
回读校验后落盘）。站点改版时用它刷新快照，再跑本文件复核。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hunter1.crawlers.registry import SITES, available_sites, build_all, build_site, get_site

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 规划 §7 M2 的验收线：≥5 个真实站点
MIN_SITES = 5


class _OfflineFetcher:
    """把固定 HTML 当作抓取结果；记录请求过的 URL。"""

    def __init__(self, html: str) -> None:
        self.html = html
        self.requested: list[str] = []

    def get_text(self, url: str, **_kwargs: object) -> str:
        self.requested.append(url)
        return self.html


def _fixture(key: str) -> str:
    path = FIXTURES / f"{key}.html"
    assert path.exists(), f"缺少站点快照 {path.name}；新增站点时要一并留快照"
    return path.read_text(encoding="utf-8")


def test_at_least_five_real_sites_registered() -> None:
    assert len(SITES) >= MIN_SITES


def test_site_keys_are_sorted_and_unique() -> None:
    assert available_sites() == sorted(SITES)
    assert len(available_sites()) == len(set(available_sites()))


def test_every_registered_site_has_a_fixture() -> None:
    missing = [key for key in available_sites() if not (FIXTURES / f"{key}.html").exists()]
    assert not missing, f"这些站点没有快照：{missing}"


@pytest.mark.parametrize("key", available_sites())
def test_site_builds_with_explicit_url(key: str) -> None:
    site = get_site(key)
    crawler = build_site(key, fetcher=_OfflineFetcher("<html></html>"))
    assert crawler.company == site.label
    # key 是进度关联的唯一标识（label 只是显示名，可能重名）
    assert crawler.key == key
    assert crawler.careers_url.startswith("http")


@pytest.mark.parametrize("key", available_sites())
def test_site_extracts_jobs_from_real_snapshot(key: str) -> None:
    """每个站点都能从真实页面快照里抽出结构化岗位。"""
    jobs = build_site(key, fetcher=_OfflineFetcher(_fixture(key))).fetch()
    assert len(jobs) >= 3, f"{key} 只解析出 {len(jobs)} 条"

    for job in jobs:
        assert job.title.strip(), f"{key} 出现空标题"
        assert job.company.strip(), f"{key} 出现空公司名"
        assert job.detail_url.startswith(("http://", "https://")), f"{key}: {job.detail_url!r}"
        assert job.source == get_site(key).label


@pytest.mark.parametrize("key", available_sites())
def test_extracted_urls_are_deduplicated(key: str) -> None:
    jobs = build_site(key, fetcher=_OfflineFetcher(_fixture(key))).fetch()
    urls = [job.detail_url for job in jobs]
    assert len(urls) == len(set(urls))


def test_unknown_site_raises_with_available_keys() -> None:
    with pytest.raises(KeyError) as excinfo:
        get_site("nope")
    assert "nope" in str(excinfo.value)
    assert available_sites()[0] in str(excinfo.value)


def test_build_all_builds_every_registered_site() -> None:
    crawlers = build_all(fetcher=_OfflineFetcher("<html></html>"))
    assert [c.company for c in crawlers] == [SITES[key].label for key in available_sites()]


def test_build_all_respects_selected_keys() -> None:
    keys = available_sites()[:2]
    crawlers = build_all(fetcher=_OfflineFetcher("<html></html>"), keys=keys)
    assert [c.company for c in crawlers] == [SITES[key].label for key in keys]


def test_job_type_reflects_site_nature() -> None:
    """实习站产出的岗位类型是「实习」，不是默认的「校招」。"""
    jobs = build_site("shixiseng", fetcher=_OfflineFetcher(_fixture("shixiseng"))).fetch()
    assert {job.job_type for job in jobs} == {"实习"}
