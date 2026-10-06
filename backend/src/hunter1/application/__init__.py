"""应用层 —— 进程边界协议的归属地。

本包只剩 `ports.py`：切片与外部世界之间的抽象（Crawler / JobRepository /
TextFetcher / LLMProvider）。所有切片依赖它是设计意图，不是越权。

原先住在这里的用例实现（抓取 / 评分 / 投递 / 求职助手）已全部归位到
`hunter1.slices.*` —— 它们曾是切片实现的逐字拷贝，会与生产代码漂移。
`applications.py` 是最后一个：随「投递」动作归属 applications 切片而删除。
"""

from __future__ import annotations

__all__: list[str] = []
