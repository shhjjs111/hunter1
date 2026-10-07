# SLICE: scoring

> 切片自述 —— 一个 Agent 只读本文件 + 根 `AGENTS.md`，即可合规地改这个切片。

## 职责（一句话）

读岗位 + 候选人画像 → 经 LLM 网关评分 → 把分数写回岗位。

## 公开面（其他切片只能从这里 import）

| 符号 | 用途 |
|---|---|
| `score_job` | 评分用例（失败抛 `ScoringError`，不返回 0 分） |
| `CandidateProfile` / `ScoreCard` / `ScoringError` | 领域模型 |
| `ProfileForm` / `ProfileView` / `ScoreView` | HTTP 形状（契约源头；路由响应都是有类型的模型） |
| `PROMPT_VERSION` / `build_user_prompt` / `SYSTEM_PROMPT` / `SCORE_SCHEMA` | 提示词（换代只动 `prompts.py`） |
| `ScoreStore` / `build_router` | 存取门面与 HTTP 面工厂 |

## 依赖

- 允许：`hunter1.platform.*`、`hunter1.domain.models`（Job，过渡期）、
  `hunter1.application.ports`（LLMProvider，进程边界协议）

## 文件

| 文件 | 职责 |
|---|---|
| `router.py` | HTTP 端点（`POST /api/scoring/{job_id}`） |
| `service.py` | 评分用例（`score_job`） |
| `prompts.py` | **提示词与口径**（与代码共置；改它 = 改这一版评分行为） |
| `models.py` | 切片自有领域模型（画像 / 评分卡 / 失败类型） |
| `store.py` | 读写岗位分数（唯一接触数据库处） |

## API 面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/scoring/profile` | 读候选人画像（**未配置时为 `null`**，不是 404 —— 未配置是初始状态） |
| PUT | `/api/scoring/profile` | 保存画像；不合法 → 422（见下「画像上限」） |
| POST | `/api/scoring/{job_id}` | 评分并写回；**409 画像未配置或不可用**、404 岗位不存在、422 模型失败 |

端点**始终挂载**：画像未配置不是「端点不存在」，而是「端点存在但状态未就绪」
（409 + 修复指引）。若改成「没配就不挂载」，前端照契约发出的 POST 会落进 SPA
回落的 `GET /{path:path}`、收到 405 —— 与真实原因无关的错误。

## 画像上限（成本闸门，不是性能优化）

画像会被**原样**拼进评分提示词（`prompts.build_user_prompt`），所以画像规模
**直接等于**每次评分请求的提示词规模。实测：5000 个关键词 + 10 万字符摘要 →
提示词 119,085 字符，一次评分就撞厂商 max_tokens 上限或产生高额费用。

| 字段 | 上限 |
|---|---|
| `keywords` | 50 条，单条 100 字符 |
| `directions` | 20 条，单条 100 字符 |
| `summary` | 2000 字符 |

约束定义在**领域模型**（`models.py` 的 `MAX_PROFILE_*`），并随契约导出
（`maxItems` / `maxLength`）。全空白条目会被丢弃 —— 否则 `["", ""]` 能通过
「至少要有一项信号」的检查，让那条不变量说谎。

### 存储里的画像不可用时

上限生效后，按旧规则存下的超大画像会变成非法数据。两个端点**刻意不同**：

| 端点 | 行为 | 理由 |
|---|---|---|
| `GET /profile` | 200 + `profile: null` + `warning` | 必须能打开表单去修；返 500 则永远修不了 |
| `POST /scoring/{id}` | 409 + 原因 | 状态未就绪就不该往下走 |

## 独立验证命令

```bash
cd backend && <python> -m pytest tests/slices/scoring -q
```

## 模型换代规程（本切片存在的意义之一）

模型能力会持续增长，评分口径要跟着变。换代时**只动 `prompts.py`**：

1. 改提示词 / 口径 → 升 `PROMPT_VERSION`；
2. 跑 `pytest tests/slices/scoring` 确认结构契约没破；
3. 用同一批岗位对照新旧版本的分数分布（**没有对照就没有换代收益的证据**）；
4. 提交时在 message 里写下这一版改了什么、为什么。

## 设计取舍（记录用）

- `store.py` 直接访问 `platform.db` 的岗位仓储；jobs 切片将来若提供评分写回
  的公开面，可改经其调用（协议不变，只换实现）。
- `CandidateProfile` 目前由**用户经「配置」页写入**，存在通用键值配置区
  （与 LLM 配置同表，`ScoreStore.load_profile/save_profile`）；组装处每请求
  经 `profile_provider` 读取，改画像**立刻生效、不必重启**（与 `llm_factory` 同理）。
