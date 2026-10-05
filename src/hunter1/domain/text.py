"""文本归一化 —— 纯函数，无任何 IO 依赖。

岗位标题归一化的用途是「同题折叠」比较：同一公司下不同书写形式的同一岗位
（如 "AI产品经理（2027校招）" 与 "ai产品经理"）应折叠为同一 key。
因此归一化必须**稳定且幂等**：`normalize(normalize(x)) == normalize(x)`。
"""

from __future__ import annotations

import re
import unicodedata

# 括号及其内容（中英文括号都覆盖；不跨括号匹配，便于循环剥离嵌套）
_PAREN = re.compile(r"[（(][^）)]*[）)]")
_WHITESPACE = re.compile(r"\s+")


def normalize_job_title(raw: str) -> str:
    """把岗位标题归一为可比较的 key。

    规则（按序）：
    1. NFKC 归一化（全角字母/数字/括号 → 半角，如 ＡＩ → AI）
    2. 剥离括号补充说明（"（2027校招）" / "(急招)"），支持嵌套
    3. 去除全部空白
    4. 转小写

    对空串、纯空白、纯括号输入返回空串。
    """
    if not isinstance(raw, str) or not raw:
        return ""

    text = unicodedata.normalize("NFKC", raw)

    # 循环剥离，使嵌套括号（(a(b)c)）也能被完全去除
    previous = None
    while previous != text:
        previous = text
        text = _PAREN.sub("", text)

    text = _WHITESPACE.sub("", text)
    return text.lower()


__all__ = ["normalize_job_title"]
