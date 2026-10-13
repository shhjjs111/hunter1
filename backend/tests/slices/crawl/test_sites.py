"""站点注册表的离线回归 —— 每个站点用真实页面快照跑一遍。

fixture 是**真实页面裁剪**（保留前 3 条 + 必要祖先链），所以这些测试
既能离线跑，又能守住「选择器还认得真实 DOM」这件事：站点改版时，
这里会红，而不是等到线上抓不到才被发现。

（从旧 `tests/crawlers/test_site_registry.py` 迁移：断言语义逐条保留。
快照已随收尾迁移归位到本目录 `fixtures/`。）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hunter1.domain.crawl import RawJob
from hunter1.slices.crawl.adapters import BaseCrawler, ListPageSpec, parse_detail_page
from hunter1.slices.crawl.sites import (
    SITES,
    SiteDefinition,
    available_sites,
    build_all,
    build_site,
    get_site,
)

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


class _CustomCrawler(BaseCrawler):
    """手写适配器：不接 `spec`（模拟「需要登录 / JSON 接口 / 多步跳转」的站点）。"""

    def fetch(self) -> list[RawJob]:
        return [self._job("手写岗位")]


def test_custom_crawler_class_extension_point_is_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文档写明的扩展点（挂自定义 `BaseCrawler` 子类）必须真的能用。

    修复前 `build_site` 无条件传 `spec=`，而 `BaseCrawler.__init__` 没有这个形参
    —— 挂上去必然 `TypeError`：声明式规格覆盖不到的站点根本没法接入。
    """
    monkeypatch.setitem(
        SITES,
        "custom",
        SiteDefinition(
            key="custom",
            label="自定义站",
            spec=ListPageSpec(
                url_template="https://custom.example/", item_selector=".j", title_selector="a"
            ),
            crawler_class=_CustomCrawler,
        ),
    )
    crawler = build_site("custom", fetcher=_OfflineFetcher("<html></html>"))
    assert isinstance(crawler, _CustomCrawler)
    assert crawler.key == "custom"
    assert [job.title for job in crawler.fetch()] == ["手写岗位"]


# ---- 详情页快照回读（JD 正文）----

#: 声明了 `detail_selector` 的站点 → 必须有对应的详情页快照。
#: 没声明的（应届生）不要求 —— 它本就抓不了详情页，见 sites.py 的注释。
_DETAIL_SITES = [key for key in available_sites() if SITES[key].spec.detail_selector]


def test_detail_sites_cover_the_expected_four() -> None:
    """守卫：能抓详情页的站点集合不能悄悄缩水。

    若某个站点的 `detail_selector` 被误删，下面那条参数化用例会**少跑一个**
    而不是报错（参数化按可用集合派生）—— 这条断言把集合本身钉住。
    """
    assert set(_DETAIL_SITES) >= {"shixiseng", "liepin_campus", "gaoxiaojob", "job910"}


def test_every_detail_site_has_a_snapshot() -> None:
    """有选择器的站点都要有 `<key>_detail.html` —— 否则新能力零回归保护。"""
    missing = [k for k in _DETAIL_SITES if not (FIXTURES / f"{k}_detail.html").exists()]
    assert not missing, f"这些站点缺少详情页快照：{missing}（跑 scripts/refresh_fixtures.py）"


@pytest.mark.parametrize("key", _DETAIL_SITES)
def test_detail_selector_extracts_jd_from_snapshot(key: str) -> None:
    """详情选择器要能从**真实页面快照**里抽出 JD 正文。

    这是「详情选择器还认得真实 DOM」的证据：站点改版时它先红，比用户发现
    「JD 全空了」早得多。

    长度下限取 50 字：实测四站正文在 76–1068 字之间。定得太高会因站点内容
    长短波动而假红；定太低（如 10）则守不住「只抓到标题」这类退化。
    """
    html = (FIXTURES / f"{key}_detail.html").read_text(encoding="utf-8")
    text = parse_detail_page(html, SITES[key].spec.detail_selector)
    assert text is not None, f"{key} 的详情选择器从快照里抽不出正文"
    assert len(text) > 50, f"{key} 只抽出 {len(text)} 字，选择器可能已退化"


def test_gaoxiaojob_detail_joins_all_blocks() -> None:
    """高校人才网详情页有**多个** `.detail-item`，必须全部拼上。

    实测：该页 4 个 `.detail-item`（基本信息 76 字 / 其他要求 724 字 /
    岗位职责 107 字 / 任职要求 158 字）。只取第一个会丢 989 字 ——
    而丢掉的恰是评分最需要的职责与要求。这条用真实快照钉住拼接语义。
    """
    html = (FIXTURES / "gaoxiaojob_detail.html").read_text(encoding="utf-8")
    text = parse_detail_page(html, SITES["gaoxiaojob"].spec.detail_selector)
    assert text is not None
    assert len(text) > 500, f"只抽出 {len(text)} 字 —— 多容器可能又退化成只取第一个"
