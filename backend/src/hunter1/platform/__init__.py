"""机制内核：零业务的横切能力（数据库 / LLM 网关 / HTTP 获取 / 自更新 / 文本）。

切片（未来的 `hunter1.slices.*`）与过渡期的旧模块都可依赖本包；
本包**不得**依赖任何业务代码。

过渡期豁免（Wave 4 完成后收紧）：`platform.db` / `platform.llm` 暂时引用
`hunter1.domain.*` 里的共享模型（Job / Message / LLMSettings 等）—— 这些模型
将在 Wave 4 随各自的切片归位，届时本包不再依赖 domain。
"""

from __future__ import annotations

__all__: list[str] = []
