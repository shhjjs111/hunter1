"""robots.txt 的抓取与判定 —— 「对站点礼貌」的机制半边。

为什么需要它：抓取层的自我定位是「对站点礼貌」，但此前只做了并发/退避治理 ——
不读 `robots.txt`、也没有请求间隔。于是「站点明确写了 don't crawl」与「站点什么都
没写」在本工具眼里一模一样，而 UA 轮换 + 自动重试只会让这件事更显眼。

实现遵循 RFC 9309 的主干：

- 每个主机取一次 `/robots.txt`（进程内缓存；**失败也缓存**，但要留痕 —— 把
  「读不到」静默当成「没限制」是方向相反的静默失败）；
- 组选择：`User-agent` 与请求 UA 做大小写不敏感的子串匹配，取**最长**匹配的组；
  都不匹配时退回 `*` 组（RFC 9309 §2.2.1）；
- 规则判定：路径**前缀**匹配，支持 `*` 通配与 `$` 结尾；最长匹配胜，长度相同时
  `Allow` 胜（RFC 9309 §2.2.2）；
- 空的 `Disallow:` 表示「不限制」（而不是「什么都不能抓」）—— 这是最常见的写法，
  判反会让所有站点都抓不动。
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_AGENT_LINE = re.compile(r"^user-agent\s*:\s*(.*)$", re.IGNORECASE)
_RULE_LINE = re.compile(r"^(allow|disallow)\s*:\s*(.*)$", re.IGNORECASE)

#: robots.txt 自身的体积上限（它不该很大；超了当读不到处理）。
ROBOTS_MAX_BYTES = 512 * 1024

#: **成功**解析出的规则缓存多久（秒）。站点改 robots 罕见，缓存久一点省请求。
DEFAULT_CACHE_TTL = 3600.0

#: **读不到** robots.txt 时的缓存时长（秒）—— 刻意短。
#:
#: 为什么失败也缓存、但不永久缓存：站点读不到自己的 robots 是它的问题，不该让本工具
#: 一轮抓取全停（所以仍按「未声明限制」放行）。但一次**瞬时故障**（DNS 抖动、连接
#: 超时、上游 502）若被永久记住，该主机在**进程剩余生命周期**里会一直按「未声明限制」
#: 处理 —— 站点恢复之后、甚至真的补写了一份 `Disallow`，我们都不会再读。
#: 短 TTL 让瞬时故障自己过期，代价只是故障期间多打几次 robots.txt。
DEFAULT_FAILURE_TTL = 60.0


@dataclass
class RobotsRules:
    """一份 robots.txt 的分组规则：`agent → [(is_allow, path_pattern), …]`。"""

    groups: dict[str, list[tuple[bool, str]]] = field(default_factory=dict)

    def allows(self, path: str, *, user_agent: str = "*") -> bool:
        """该路径是否允许该 UA 抓取。"""
        return _allows_path(_select_group(self.groups, user_agent), path)


def parse_robots(text: str) -> RobotsRules:
    """把 robots.txt 解析成分组规则。

    组 = 连续的 `User-agent:` 行 + 其后直到下一个组的所有 `Allow`/`Disallow` 行。
    `#` 起注释；字段名大小写不敏感；不认识的字段忽略（RFC 9903 的容错要求：
    一份 robots.txt 里有别的扩展字段是常态）。
    """
    groups: dict[str, list[tuple[bool, str]]] = {}
    current_agents: list[str] = []
    expecting_agents = True

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        agent = _AGENT_LINE.match(line)
        if agent is not None:
            token = agent.group(1).strip().lower()
            if not expecting_agents:
                # 上一组已经收到过规则 → 这一行开启新组
                current_agents = []
                expecting_agents = True
            if token:
                current_agents.append(token)
                groups.setdefault(token, [])
            continue

        rule = _RULE_LINE.match(line)
        if rule is not None and current_agents:
            expecting_agents = False
            is_allow = rule.group(1).lower() == "allow"
            pattern = rule.group(2).strip()
            for token in current_agents:
                groups[token].append((is_allow, pattern))

    return RobotsRules(groups=groups)


def _select_group(
    groups: dict[str, list[tuple[bool, str]]], user_agent: str
) -> list[tuple[bool, str]]:
    """挑出适用于该 UA 的规则组：最长子串匹配，回退到 `*`。"""
    ua = (user_agent or "").lower()
    best_token = ""
    best_rules: list[tuple[bool, str]] | None = None
    for token, rules in groups.items():
        if token == "*" or not token or token not in ua:
            continue
        if len(token) > len(best_token):
            best_token, best_rules = token, rules
    if best_rules is not None:
        return best_rules
    return groups.get("*", [])


def _allows_path(rules: list[tuple[bool, str]], path: str) -> bool:
    """给定规则组，判断路径是否允许抓取：最长匹配胜，平局 Allow 胜。"""
    best_length = -1
    allowed = True
    for is_allow, pattern in rules:
        if not _matches(path, pattern):
            continue
        # 用「模式长度」近似具体程度（RFC 9309 §2.2.2 的最长匹配；Google 同此）
        if len(pattern) > best_length or (len(pattern) == best_length and is_allow):
            best_length = len(pattern)
            allowed = is_allow
    return allowed


def _matches(path: str, pattern: str) -> bool:
    """路径是否命中该模式（前缀匹配 + `*` 通配 + `$` 结尾锚）。"""
    if not pattern:
        return False  # 空模式不匹配任何东西：空 Disallow 即「不限制」
    body, anchored = (pattern[:-1], True) if pattern.endswith("$") else (pattern, False)
    escaped = ".*".join(re.escape(part) for part in body.split("*"))
    return re.match(f"^{escaped}{'$' if anchored else ''}", path) is not None


class RobotsCache:
    """按主机缓存 robots.txt 的判定器（成功与失败各自带 TTL）。

    `fetch_text` 是取原文的回调；取不到时应返回 None（4xx / 5xx / 网络错误 / 过大），
    此时按**未声明限制**处理并在 stderr 留一行 —— 站点读不到自己的 robots 是它的
    问题，不该让本工具一轮抓取全停，但也不能不声不响。

    两类结果**分别**缓存：成功的用 `cache_ttl`（长），失败的用 `failure_ttl`（短）——
    为什么失败不能永久缓存，见 `DEFAULT_FAILURE_TTL`。
    """

    def __init__(
        self,
        fetch_text: Callable[[str], str | None],
        *,
        clock: Callable[[], float] = time.monotonic,
        cache_ttl: float = DEFAULT_CACHE_TTL,
        failure_ttl: float = DEFAULT_FAILURE_TTL,
    ) -> None:
        self._fetch_text = fetch_text
        self._clock = clock
        self._cache_ttl = cache_ttl
        self._failure_ttl = failure_ttl
        #: host → (规则, 过期时刻)；时刻由注入的 `clock` 计（测试可推进）。
        self._by_host: dict[str, tuple[RobotsRules, float]] = {}

    def allows(self, url: str, *, user_agent: str = "*") -> bool:
        split = urlsplit(url)
        if split.scheme not in ("http", "https") or not split.hostname:
            return True  # 非 http(s)（畸形 URL / file://）不适用 robots
        host = f"{split.scheme}://{split.netloc.lower()}"

        rules = self._rules_for(host)

        path = split.path or "/"
        if split.query:
            path = f"{path}?{split.query}"
        return rules.allows(path, user_agent=user_agent)

    def _rules_for(self, host: str) -> RobotsRules:
        """取该主机的规则；缓存过期（或不存在）时重新拉取。"""
        now = self._clock()
        cached = self._by_host.get(host)
        if cached is not None and cached[1] > now:
            return cached[0]
        rules, loaded_ok = self._load(host)
        ttl = self._cache_ttl if loaded_ok else self._failure_ttl
        self._by_host[host] = (rules, now + ttl)
        return rules

    def _load(self, host: str) -> tuple[RobotsRules, bool]:
        """拉取并解析 robots.txt；返回 (规则, **是否真的读到了**)。

        第二个返回值决定缓存时长（见 `_rules_for`）—— 「读到了一份没有规则的
        robots」与「压根没读到」必须分开，前者是站点在声明「不限制」，后者只是
        这一次没拿到。
        """
        text = self._fetch_text(f"{host}/robots.txt")
        if text is None:
            print(f"警告：读不到 {host}/robots.txt，本轮按「未声明限制」处理。", file=sys.stderr)
            return RobotsRules(), False
        return parse_robots(text), True


__all__ = [
    "DEFAULT_CACHE_TTL",
    "DEFAULT_FAILURE_TTL",
    "ROBOTS_MAX_BYTES",
    "RobotsCache",
    "RobotsRules",
    "parse_robots",
]
