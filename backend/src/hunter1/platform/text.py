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


def redact_secret(text: str, secret: str) -> str:
    """把文本里出现的**密钥**抹成 `***`。

    用途固定的场景：厂商的 4xx/5xx 响应体常把 `Authorization` 头原样回显，而
    这段文案会显示给用户、也可能被用户贴给维护者 —— 它绝不能含完整密钥。
    掩码形态与 `LLMSettings.masked_key()` 一致（`***`）。

    放在 platform 层是因为调用方横跨多个切片（LLM 客户端、settings 配置页…），
    各写一份必然分叉；空/纯空白密钥原样返回（不误伤）。
    """
    key = (secret or "").strip()
    if key:
        text = text.replace(key, "***")
    return text


#: JD（岗位描述）在提示词 / 工具结果里的围栏标记。抓来的内容不可信 —— 必须被
#: 围栏包起来，且内容不能自己闭合围栏（见 `fence_untrusted_jd`）。
JD_FENCE_OPEN = "<<<JD"
JD_FENCE_CLOSE = "JD>>>"


def fence_untrusted_jd(text: str, *, limit: int) -> str:
    """把抓取来的 JD 变成「无法逃逸出围栏、且长度受控」的文本（不含围栏本身）。

    做两件事：

    1. **剥掉围栏标记** —— 否则内容里写一行 `JD>>>` 就能提前闭合围栏，把后续文本
       伪装成画像 / 指令段落（实测：注入的 JD 能让评分段被追加一段「给这个岗位
       100 分」）；
    2. **按 `limit` 截断** —— JD 长度不受控，整段灌进提示词 / 工具结果会让成本
       闸门恰好用错地方（画像有上限，最大的那个输入反而没有）。

    围栏本身由调用方用 `JD_FENCE_OPEN` / `JD_FENCE_CLOSE` 拼上 —— 标记只此一处，
    改围栏形态时不会漏掉某个调用方（评分、求职助手共用这一份）。
    """
    for marker in (JD_FENCE_OPEN, JD_FENCE_CLOSE):
        text = text.replace(marker, "")
    text = text.strip()
    if len(text) > limit:
        text = text[:limit] + "\n…（岗位描述过长，已截断）"
    return text


__all__ = [
    "JD_FENCE_CLOSE",
    "JD_FENCE_OPEN",
    "fence_untrusted_jd",
    "normalize_job_title",
    "redact_secret",
]
