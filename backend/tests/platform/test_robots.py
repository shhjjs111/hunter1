"""robots.txt 解析与判定测试 —— 纯函数 + 假取文回调，全程离线。"""

from __future__ import annotations

import pytest

from hunter1.platform.fetch.robots import RobotsCache, parse_robots

SAMPLE = """\
# 注释行
User-agent: *
Disallow: /private
Allow: /private/public

User-agent: BadBot
Disallow: /
"""


class TestParse:
    def test_wildcard_group_rules(self) -> None:
        rules = parse_robots(SAMPLE)
        assert rules.allows("/jobs", user_agent="Mozilla/5.0") is True
        assert rules.allows("/private/x", user_agent="Mozilla/5.0") is False
        # Allow 比 Disallow 更具体 → 胜出
        assert rules.allows("/private/public/a", user_agent="Mozilla/5.0") is True

    def test_comments_and_unknown_fields_are_ignored(self) -> None:
        rules = parse_robots(
            "User-agent: *\n# 注释\nSitemap: https://a.com/sitemap.xml\nDisallow: /x # 尾注\n"
        )
        assert rules.allows("/x", user_agent="ua") is False
        assert rules.allows("/y", user_agent="ua") is True

    def test_empty_disallow_means_no_restriction(self) -> None:
        """`Disallow:` 空值是「不限制」—— 判反会让所有站点都抓不动。"""
        rules = parse_robots("User-agent: *\nDisallow:\n")
        assert rules.allows("/anything", user_agent="ua") is True

    def test_ua_specific_group_wins_over_wildcard(self) -> None:
        rules = parse_robots(SAMPLE)
        assert rules.allows("/", user_agent="My-BadBot/1.0") is False

    def test_multiple_user_agent_lines_share_one_group(self) -> None:
        rules = parse_robots("User-agent: A\nUser-agent: B\nDisallow: /x\n")
        assert rules.allows("/x", user_agent="A") is False
        assert rules.allows("/x", user_agent="B") is False
        assert rules.allows("/x", user_agent="C") is True  # 没匹配到任何组

    def test_wildcard_and_end_anchor_patterns(self) -> None:
        rules = parse_robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /tmp/*\n")
        assert rules.allows("/a/b.pdf", user_agent="ua") is False
        assert rules.allows("/a/b.pdf?x=1", user_agent="ua") is True  # `$` 锚在结尾
        assert rules.allows("/tmp/anything", user_agent="ua") is False

    def test_empty_document_allows_everything(self) -> None:
        rules = parse_robots("")
        assert rules.allows("/x", user_agent="ua") is True


class TestCache:
    def test_fetches_once_per_host(self) -> None:
        calls: list[str] = []

        def fetch(url: str) -> str | None:
            calls.append(url)
            return "User-agent: *\nDisallow: /private"

        cache = RobotsCache(fetch)
        assert cache.allows("https://a.com/jobs", user_agent="ua") is True
        assert cache.allows("https://a.com/other", user_agent="ua") is True
        assert cache.allows("https://a.com/private", user_agent="ua") is False
        assert calls == ["https://a.com/robots.txt"]

    def test_query_string_participates_in_matching(self) -> None:
        cache = RobotsCache(lambda url: "User-agent: *\nDisallow: /jobs?q=")
        assert cache.allows("https://a.com/jobs?q=1", user_agent="ua") is False
        assert cache.allows("https://a.com/jobs", user_agent="ua") is True

    def test_unavailable_robots_allows_but_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        cache = RobotsCache(lambda url: None)
        assert cache.allows("https://a.com/jobs", user_agent="ua") is True
        assert "robots.txt" in capsys.readouterr().err

    def test_non_http_urls_are_not_checked(self) -> None:
        def fetch(url: str) -> str | None:  # pragma: no cover - 不该被调用
            raise AssertionError("非 http(s) 不该去取 robots.txt")

        cache = RobotsCache(fetch)
        assert cache.allows("file:///tmp/x", user_agent="ua") is True
        assert cache.allows("not a url", user_agent="ua") is True

    def test_different_hosts_are_cached_separately(self) -> None:
        def fetch(url: str) -> str | None:
            return "User-agent: *\nDisallow: /private" if "a.com" in url else None

        cache = RobotsCache(fetch)
        assert cache.allows("https://a.com/private", user_agent="ua") is False
        assert cache.allows("https://b.com/private", user_agent="ua") is True

    def test_failure_is_not_cached_forever(self, capsys: pytest.CaptureFixture[str]) -> None:
        """读不到 robots 只**短**缓存（报告次要项）。

        原先失败结果被永久记住：一次瞬时故障（DNS 抖动、连接超时）会让该主机在
        **进程剩余生命周期**里一直按「未声明限制」处理 —— 站点恢复、甚至补写了
        `Disallow` 之后我们都不会再读。
        """
        calls: list[str] = []
        state = {"ok": False}

        def fetch(url: str) -> str | None:
            calls.append(url)
            return "User-agent: *\nDisallow: /private" if state["ok"] else None

        now = {"t": 0.0}
        cache = RobotsCache(fetch, clock=lambda: now["t"], failure_ttl=60.0)

        assert cache.allows("https://a.com/private", user_agent="ua") is True  # 读不到 → 放行
        assert len(calls) == 1

        now["t"] = 59.0  # 失败缓存未过期 → 不重取
        assert cache.allows("https://a.com/private", user_agent="ua") is True
        assert len(calls) == 1

        state["ok"] = True  # 站点恢复
        now["t"] = 61.0  # 失败缓存过期
        assert cache.allows("https://a.com/private", user_agent="ua") is False
        assert len(calls) == 2
        capsys.readouterr()

    def test_successful_rules_are_cached_for_their_ttl(self) -> None:
        """成功的结果缓存久（`cache_ttl`）—— 别为一份 robots 反复打站点。"""
        calls: list[str] = []

        def fetch(url: str) -> str | None:
            calls.append(url)
            return "User-agent: *\nDisallow: /private"

        now = {"t": 0.0}
        cache = RobotsCache(fetch, clock=lambda: now["t"], cache_ttl=3600.0)

        assert cache.allows("https://a.com/private", user_agent="ua") is False
        now["t"] = 3599.0
        assert cache.allows("https://a.com/x", user_agent="ua") is True
        assert len(calls) == 1, "TTL 内不该重取"

        now["t"] = 3601.0
        assert cache.allows("https://a.com/y", user_agent="ua") is True
        assert len(calls) == 2, "TTL 过后应重取"
