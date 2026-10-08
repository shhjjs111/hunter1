"""文本归一化 —— 纯函数，无任何 IO 依赖。

岗位标题归一化的用途是「同题折叠」比较：同一公司下不同书写形式的同一岗位
（如 "AI产品经理（2027校招）" 与 "ai产品经理"）应折叠为同一 key。
因此归一化必须**稳定且幂等**：`normalize(normalize(x)) == normalize(x)`。
"""

from __future__ import annotations

import re
import unicodedata

# 括号及其内容（中英文括号都覆盖）。body **不允许再含括号** —— 这样一次 sub
# 只吃掉最内层那一对，配合下面的循环从内向外逐层剥离嵌套。
# 反例（修复前 `[^）)]*`）：body 会吞掉内层左括号、撞到内层第一个 `)` 就收尾，
# 于是内层之后的右括号再也配不上对、残留下来 ——
# normalize_job_title("AI产品经理（2027校招（提前批））") 得到 "ai产品经理)"，
# 同一岗位两种写法折叠失败（Job.title_key 不同）。
_PAREN = re.compile(r"[（(][^（）()]*[）)]")
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
