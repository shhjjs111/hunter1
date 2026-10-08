"""抓取领域类型与纯规则 —— 无任何 IO 依赖。

`RawJob` 是适配器产出的「原始岗位」，刻意保持与存储模型（`Job`）分离：
适配器只管从页面里抽出事实，不关心入库与否、是否已存在、评分多少。

`job_identity` 给出一条岗位的**稳定身份**：同一岗位无论被哪个适配器、在哪一天
抓到，都应得到同一个 id —— 这是「同题折叠」与增量更新的前提。
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from hunter1.platform.text import normalize_job_title

# 跟踪参数：只影响来源统计，不影响「是不是同一个岗位」
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "from",
        "fromsource",
        "ctmid",
        "spm",
        "share_token",
        "ref",
    }
)


class RawJob(BaseModel):
    """适配器产出的原始岗位事实。"""

    model_config = ConfigDict(extra="forbid")

    company: str
    title: str
    detail_url: str
    city: str | None = None
    job_type: str = "校招"
    jd_raw: str | None = None
    published_at: str | None = None
    link_kind: str = "detail"
    source: str | None = None
    campaign_text: str | None = None
    tags: list[str] = Field(default_factory=list)


def normalize_detail_url(url: str) -> str:
    """把岗位详情 URL 归一为可比较形式。

    规则：
    1. 已知跳转中间页（51job 的 apply.aspx）还原为真正的详情页
    2. 非 http(s) 的「链接」判空 —— 真实站点里列表项常挂 `javascript:void(0)`，
       真链接由 JS 绑定；这类不是可用的详情页地址
    3. scheme / host 小写，path 大小写保留（有些站点 path 大小写敏感）
    4. 去掉 fragment —— **除非它是 hash 路由**（`#/job/123`、`#!/job/123`）：
       SPA 站点的岗位身份就在 fragment 里，丢掉它会让整页岗位归一成同一个地址
       （也是同一个 `JobIdentity` → 除首条外全部被当成重复丢掉，且链接指向列表页）。
    5. 去掉跟踪类查询参数，保留其余（岗位 id 常在 query 里，不能一刀切删）
    6. 去掉末尾斜杠
    """
    raw = (url or "").strip()
    if not raw:
        return ""

    rewritten = _rewrite_intermediary(raw)
    parts = urlsplit(rewritten)

    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return ""

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or parts.path

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(query_pairs),
            _kept_fragment(parts.fragment),
        )
    )


def _kept_fragment(fragment: str) -> str:
    """fragment 是否该保留（hash 路由）—— 返回交给 `urlunsplit` 的那一段。

    ⚠ 不带前导 `#`：`urlunsplit` 自己会加，多一个就变成 `##/job/1`。
    """
    if fragment.startswith("/") or fragment.startswith("!/"):
        return fragment
    return ""


def _rewrite_intermediary(url: str) -> str:
    """把已知的申请跳转页还原为岗位详情页。"""
    parts = urlsplit(url)
    if (
        parts.netloc.casefold() == "xyz.51job.com"
        and parts.path.casefold() == "/external/apply.aspx"
    ):
        job_ids = [value for key, value in parse_qsl(parts.query) if key.lower() == "jobid"]
        if job_ids and job_ids[0].isdigit():
            return f"https://jobs.51job.com/all/{job_ids[0]}.html"
    return url


def job_identity(
    *,
    detail_url: str,
    company: str | None = None,
    title: str | None = None,
) -> str:
    """计算岗位的稳定身份（sha256 十六进制）。

    优先用归一化后的 URL；URL 缺失时退化为「公司 + 归一化标题」。
    两者都缺则抛 ValueError（无法为一个没有身份的东西建档）。
    """
    normalized = normalize_detail_url(detail_url)
    if normalized:
        basis = f"url:{normalized}"
    elif company and title:
        basis = f"ct:{company.strip()}:{normalize_job_title(title)}"
    else:
        raise ValueError("job identity requires either a detail_url or (company, title)")
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


__all__ = ["RawJob", "job_identity", "normalize_detail_url"]
