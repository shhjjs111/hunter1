"""domain.crawl 单元测试 —— 抓取领域类型，纯函数，无 IO。

TDD：本文件先于实现编写（当时为 RED；实现已落地，此后应保持全绿）。
"""

from __future__ import annotations

import pytest

from hunter1.domain.crawl import RawJob, job_identity, normalize_detail_url


class TestRawJob:
    def test_minimal_construction(self) -> None:
        job = RawJob(company="字节跳动", title="AI产品经理", detail_url="https://a.com/1")
        assert job.title == "AI产品经理"
        assert job.city is None
        assert job.jd_raw is None
        assert job.link_kind == "detail"

    def test_mutable_default_not_shared(self) -> None:
        first = RawJob(company="A", title="t", detail_url="https://a.com/1")
        second = RawJob(company="B", title="t", detail_url="https://b.com/1")
        first.tags.append("x")
        assert second.tags == []


class TestNormalizeDetailUrl:
    def test_strips_tracking_query_and_fragment(self) -> None:
        raw = "https://jobs.example.com/apply/123?utm_source=x&from=y#section"
        assert normalize_detail_url(raw) == "https://jobs.example.com/apply/123"

    def test_lowercases_scheme_and_host_keeps_path_case(self) -> None:
        raw = "HTTPS://Jobs.Example.COM/Apply/AbC"
        assert normalize_detail_url(raw) == "https://jobs.example.com/Apply/AbC"

    def test_drops_trailing_slash(self) -> None:
        assert normalize_detail_url("https://a.com/jobs/") == "https://a.com/jobs"

    def test_keeps_meaningful_query(self) -> None:
        """不能一刀切删掉 query —— 有些站点的岗位 id 就在 query 里。"""
        raw = "https://a.com/detail?jobId=998"
        assert normalize_detail_url(raw) == "https://a.com/detail?jobId=998"

    def test_51job_apply_intermediary_is_rewritten(self) -> None:
        """51job 的跳转中间页应还原为岗位详情页（旧系统同款规则）。"""
        raw = "https://xyz.51job.com/external/apply.aspx?jobid=123456&ctmid=9"
        assert normalize_detail_url(raw) == "https://jobs.51job.com/all/123456.html"

    def test_blank_returns_blank(self) -> None:
        assert normalize_detail_url("") == ""
        assert normalize_detail_url("   ") == ""

    @pytest.mark.parametrize(
        "raw",
        [
            "javascript:void(0)",
            "javascript:openDetail('123')",
            "#",
            "mailto:hr@example.com",
            "tel:+861000000000",
        ],
    )
    def test_non_http_schemes_are_rejected(self, raw: str) -> None:
        """非 http(s) 的「链接」不是岗位详情页 —— 必须判空，不能当 URL 用。

        真实站点常见：列表项挂着 `javascript:void(0)`，真链接由 JS 绑定。
        若不拦，会产出 `https://site.com/javascript:void(0)` 这种垃圾 URL。
        """
        assert normalize_detail_url(raw) == ""


class TestJobIdentity:
    def test_same_url_yields_same_identity(self) -> None:
        url = "https://a.com/1"
        assert job_identity(detail_url=url) == job_identity(detail_url=url)

    def test_tracking_params_do_not_change_identity(self) -> None:
        a = job_identity(detail_url="https://a.com/1?utm_source=x")
        b = job_identity(detail_url="https://a.com/1")
        assert a == b

    def test_different_urls_differ(self) -> None:
        assert job_identity(detail_url="https://a.com/1") != job_identity(
            detail_url="https://a.com/2"
        )

    def test_falls_back_to_company_title_when_no_url(self) -> None:
        a = job_identity(detail_url="", company="字节跳动", title="AI产品经理")
        b = job_identity(detail_url="", company="字节跳动", title="AI产品经理（2027校招）")
        # 标题归一化后相同 → 身份相同（同题折叠）
        assert a == b

    def test_no_url_and_no_company_title_raises(self) -> None:
        with pytest.raises(ValueError):
            job_identity(detail_url="")

    def test_identity_is_stable_hex_string(self) -> None:
        identity = job_identity(detail_url="https://a.com/1")
        assert len(identity) == 64  # sha256 hex
        assert all(c in "0123456789abcdef" for c in identity)
