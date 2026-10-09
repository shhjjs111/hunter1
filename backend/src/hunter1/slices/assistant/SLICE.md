# SLICE: assistant

> 切片自述 —— 一个 Agent 只读本文件 + 根 `AGENTS.md`，即可合规地改这个切片。

## 职责（一句话）

跑 assistant agent 循环（用户消息 → 模型 → 工具 → 结果回灌 → 结论），
并把过程以一次性或 SSE 流式两种方式交出去。

## 公开面（其他切片只能从这里 import）

| 符号 | 用途 |
|---|---|
| `run_turn` / `run_turn_stream` | 一轮对话（两种交付方式，同一套语义） |
| `AssistantResult` / `ToolStarted` / `ToolFinished` / `TurnDone` | 结果与事件类型 |
| `build_tools` | 工具集构造（jobs/applications 仓储由调用方注入） |
| `Tool` / `ToolRegistry` / `tool` | 工具协议（新增工具 = 写函数 + `@tool`） |
| `ConversationStore` / `build_router` | 会话存取与 HTTP 面工厂 |

## 依赖

- 允许：`hunter1.platform.*`、`hunter1.domain.*`（Message/Role/ToolCall 是共享会话语言）、
  `hunter1.application.ports`（LLMProvider / 仓储协议，进程边界）

## 文件

| 文件 | 职责 |
|---|---|
| `router.py` | HTTP 端点（会话列表/消息、`POST /assistant/turn`、`POST /assistant/stream`） |
| `schemas.py` | API 模型（契约唯一事实来源；含分页的 `ConversationListResponse`） |
| `service.py` | agent 循环（含刹车 `max_iterations`、工具失败不中断对话） |
| `tools.py` | 工具注册表（类型注解 → JSON Schema，异常转 `ToolResult(error=...)`） |
| `job_tools.py` | 助手可调用的只读工具（查岗位/投递） |
| `store.py` | 会话持久化门面 |

## API 面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/assistant/conversations` | 会话列表（**分页**：`?page=&page_size=` → `{items, total, page, page_size, has_next}`；`page_size` ≤ `MAX_PAGE_SIZE=50`，越界 422） |
| GET | `/api/assistant/conversations/{id}` | 会话消息（404 不存在） |
| POST | `/api/assistant/turn` | 一次性对话（422 上游模型失败，且不落库；非契约异常 → 500；**404 会话不存在**） |
| POST | `/api/assistant/stream` | SSE 流式对话（失败走 `error` 事件；**404 会话不存在，在开流之前**） |

**`conversation_id` 的语义（读写一致）**：请求带了一个**非空**但查不到的
`conversation_id` 时，两个写端点与读端点一样返回 **404**。原先写端点会静默新建
会话（历史为空），而读端点为同一资源返回 404 —— 同一资源「不存在」在读写两面
语义相反，拼错或已删除的 id 永不暴露。省略 `conversation_id`（空串）才是「新会话」。

## SSE 事件契约

| type | 字段 | 说明 |
|---|---|---|
| `text` | `text` | 文本增量（逐字输出） |
| `tool_start` | `name` / `arguments` | 工具开始执行（界面显示「正在查询…」） |
| `tool_end` | `name` / `ok` / `content` / `error` | 工具结果（content 截断至 1000 字符） |
| `done` | `conversation_id` / `reply` / `truncated` / `degraded` | 整轮结束（**总是最后一个**） |
| `error` | `message` | 失败（响应已开始，只能以事件交出去） |

**截断的两种来源**（都必须在界面上说出来，否则「半截回答」与「完整回答」无法区分）：

- `finish_reason == "length"`：上游被 token 上限截断 → 回复尾部追加「回答因长度上限
  被截断」的提示（见 `service.py` 的 `_LENGTH_TRUNCATED_NOTICE`）；
- `truncated`：工具调用轮次达到 `max_iterations` 刹车 → `done.truncated` 为真。

「流没收到 `[DONE]`」**不**当作失败：大量兼容网关就这么收尾，属既有容忍设计。
但流若**既没 done 也没 error** 地安静结束，前端会提示「连接中断，本轮没有保存」
（后端在 yield `done` 之前才落库）。

## 独立验证命令

```bash
cd backend && <python> -m pytest tests/slices/assistant -q
```

## 设计取舍（记录用）

- `Message` / `Role` / `ToolCall` / `ToolResult`（`domain/assistant.py`）**刻意留在共享位置**：
  `platform/db`（会话持久化）与 `platform/llm`（OpenAI 消息序列化）都要用它们 ——
  归位到本切片会让 platform 反向依赖 slices，破坏分层。
  终态的合理位置是 `platform/llm`（它们描述的正是模型消息协议），届时一并迁移。
- 工具集在组装处以 `build_tools(jobs=..., applications=...)` 构造后注入 router；
  切片内不持有全局状态。
