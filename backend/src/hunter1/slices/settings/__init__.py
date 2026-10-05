"""settings 切片 —— LLM 配置的读写与连通性探测。

（规划期未把它列为切片，但前端需要经 API 配置模型 —— 否则「前后端完全分离」
留了个洞：配置必须回退到旧 SSR 表单。此偏离已记入 SLICE.md。）
"""

from hunter1.slices.settings.router import build_router
from hunter1.slices.settings.store import SettingsStore

__all__ = ["SettingsStore", "build_router"]
