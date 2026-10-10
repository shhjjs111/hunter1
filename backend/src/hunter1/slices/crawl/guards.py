"""抓取守卫 —— 把「被风控拦下」从「抓到 0 条」里分出来。

为什么需要它（实测，不是假想）：BOSS直聘在连续请求后返回 HTTP 200 的
`<title>安全验证 - BOSS直聘</title>` 风控页。对解析器来说，这就是一个
没有岗位的页面 —— 和「今天确实没有新岗位」长得一模一样。若不加区分，
用户会以为站点空了，而实际上是他被拦了，**且永远不会知道**。

这与 scoring 切片里「没评上 ≠ 评了 0 分」是同一条原则：
**「没有数据」和「拿不到数据」是两件事，混在一起会让整条链路失去信噪比。**

判据取「页面级」信号，不看正文 —— 否则岗位标题里写着「安全验证工程师」
的正常页面会被误判。
"""

from __future__ import annotations

import re

# 标题里出现这些词，基本可断定是拦截/挑战页（大小写不敏感，子串匹配）。
# 刻意收窄：像「验证码」这种会出现在正常岗位标题里的词不进列表。
#
# 公开（去掉下划线）是**刻意**的：用例要断言「词表与覆盖一一对应」——
# 私有化之后「漏了一个信号」就无从查证（实测：7 个结构标记此前零正向用例，
# 整表删空测试全绿）。
BLOCK_TITLE_SIGNALS: tuple[str, ...] = (
    "安全验证",
    "人机验证",
    "异常访问",
    "访问受限",
    "访问过于频繁",
    "just a moment",
    "attention required",
    "access denied",
    "checking your browser",
    "enable javascript and cookies",
)

# 挑战页常见的结构性标记（比标题文本更稳定，且不会出现在正常内容里）。
BLOCK_MARKUP_SIGNALS: tuple[str, ...] = (
    "challenge-platform",
    "cf-challenge",
    "cf_chl_opt",
    "geetest",
    "/verify-slider",
    "grecaptcha",
    "hcaptcha",
)

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class CrawlBlockedError(RuntimeError):
    """页面是拦截/挑战页，不是内容页。带机器可读的 `signal` 与 `url`。"""

    def __init__(self, signal: str, url: str) -> None:
        super().__init__(f"blocked_by_{signal}: {url}")
        self.signal = signal
        self.url = url


class CrawlEmptyPageError(RuntimeError):
    """**首页**一个岗位都没解析出来。

    与 `CrawlBlockedError` 同属「拿不到数据」那一类，但成因不同：没有命中拦截特征，
    只是我们的选择器在这个页面上什么都没匹配到。可能是站点改版，也可能是这家公司
    今天确实没有岗位 —— 两种情况的**处理动作是同一个**：不许当成「抓取成功，0 条」。

    为什么要抛而不是返回 `[]`：解析器返回空列表时，上层看到的和「今天真没岗位」
    一模一样，界面显示抓取成功。用户以为站点空了，而实际上是读不懂页面了，
    **且永远不会知道**（同 guards 模块开头那个 BOSS 拦截页的教训）。
    """

    def __init__(self, url: str) -> None:
        super().__init__(
            f"empty_first_page: {url} —— 首页没有解析出任何岗位："
            "要么站点今天确实没有岗位，要么页面结构变了（选择器失效）。请人工确认。"
        )
        self.url = url


def detect_blocking(html: str) -> str | None:
    """命中拦截特征则返回信号名，否则 None。"""
    if not html:
        return None

    match = _TITLE.search(html)
    if match is not None:
        title = match.group(1).strip().casefold()
        for signal in BLOCK_TITLE_SIGNALS:
            if _title_is_challenge(title, signal):
                return signal

    lowered = html.casefold()
    for signal in BLOCK_MARKUP_SIGNALS:
        if signal in lowered:
            return signal
    return None


def _title_is_challenge(title: str, signal: str) -> bool:
    """标题是否**就是**一个挑战页标题。

    只认「标题以信号开头、且其后紧跟分隔符或结束」这一种形态：
    `安全验证 - BOSS直聘` 命中，而 `安全验证工程师招聘 - 示例` 不命中 ——
    后者是一个正常岗位列表页，标题里恰好含有这几个字。
    """
    if not title.startswith(signal):
        return False
    rest = title[len(signal) :]
    return rest == "" or not rest[0].isalnum()


def ensure_not_blocked(html: str, *, url: str) -> None:
    """页面像拦截页就抛 `CrawlBlockedError`。"""
    signal = detect_blocking(html)
    if signal is not None:
        raise CrawlBlockedError(signal, url)


__all__ = [
    "BLOCK_MARKUP_SIGNALS",
    "BLOCK_TITLE_SIGNALS",
    "CrawlBlockedError",
    "CrawlEmptyPageError",
    "detect_blocking",
    "ensure_not_blocked",
]
