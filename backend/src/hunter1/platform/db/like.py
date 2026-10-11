"""LIKE 匹配的转义 —— 前缀/包含查询共用的那一处。

SQLite 的 LIKE 默认把 `%`（任意串）与 `_`（任意单字符）当通配符：标题归一化不剥离
标点，所以标题里含这些字符、或用户拿它们搜索时，会匹配到意料之外的行。显式转义后
按**字面**匹配（配合 `escape="\\"`）。

**转义只管「哪个字符」，不管「大小写」。** SQLite 的 LIKE 对 ASCII 默认大小写不敏感
（`LIKE` 自带 NOCASE 语义）：`get_by_prefix("J1")` 会命中 `j1…`。本模块与所有调用方
都**没有**加 `COLLATE BINARY` / `PRAGMA case_sensitive_like` —— 所以「前缀匹配」
实际是大小写不敏感的。当下 id 全是小写 uuid hex，无实际影响；把「大小写敏感」当成
语义前提之前，先在这里显式加 `COLLATE BINARY` 并补测试。

单独一个模块而不是留在某个仓储里：岗位与投递两处都要用（前缀查找 id），
复制一份迟早会漂移。
"""

from __future__ import annotations


def escape_like(text: str) -> str:
    """转义 LIKE 通配符（`\\`、`%`、`_`）。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


__all__ = ["escape_like"]
