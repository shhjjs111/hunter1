"""assistant 切片 —— 助手循环 + 工具协议 + 会话。

公开面（其他切片只允许从这里 import 符号）：

- `run_turn` / `run_turn_stream`：一轮对话（一次性 / 流式）
- `ToolStarted` / `ToolFinished` / `TurnDone` / `AssistantResult`：事件与结果类型
- `build_tools`：工具集构造（依赖仓储由调用方给）
- `Tool` / `ToolRegistry` / `tool`：工具协议
- `ConversationStore` / `build_router`

详见 `SLICE.md`。
"""

from hunter1.slices.assistant.job_tools import SEARCH_LIMIT_MAX, build_tools
from hunter1.slices.assistant.router import (
    DEFAULT_HISTORY_LIMIT,
    FALLBACK_REPLY,
    build_router,
)
from hunter1.slices.assistant.schemas import (
    ConversationMessageView,
    ConversationSummary,
    StreamRequest,
    TurnResponse,
)
from hunter1.slices.assistant.service import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_SYSTEM_PROMPT,
    AssistantEvent,
    AssistantResult,
    ToolFinished,
    ToolStarted,
    TurnDone,
    run_turn,
    run_turn_stream,
)
from hunter1.slices.assistant.store import ConversationStore
from hunter1.slices.assistant.tools import Tool, ToolRegistry, tool

__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_SYSTEM_PROMPT",
    "FALLBACK_REPLY",
    "SEARCH_LIMIT_MAX",
    "AssistantEvent",
    "AssistantResult",
    "ConversationMessageView",
    "ConversationStore",
    "ConversationSummary",
    "StreamRequest",
    "Tool",
    "ToolFinished",
    "ToolRegistry",
    "ToolStarted",
    "TurnDone",
    "TurnResponse",
    "build_router",
    "build_tools",
    "run_turn",
    "run_turn_stream",
    "tool",
]
