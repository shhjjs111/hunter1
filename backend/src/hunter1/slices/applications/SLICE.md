# SLICE: applications

> 切片自述 —— 一个 Agent 只读本文件 + 根 `AGENTS.md`，即可合规地改这个切片。

## 职责（一句话）

投递记录的事实（列表 / 阶段推进 / 删除）—— 「我做过的事」的完整生命周期。

## 公开面（其他切片只能从这里 import）

| 符号 | 用途 |
|---|---|
| `ApplicationStore` | 数据存取门面（唯一接触数据库的地方） |
| `build_router` | HTTP 面工厂；组装处注入 store、jobs、clock |
| `schemas` | API 模型（契约源头 → `contracts/openapi.json`） |
| `service` | 用例函数：`apply_to_job` / `new_application` / `change_stage` |

## 依赖

- 允许：`hunter1.platform.*`（机制内核）、`hunter1.domain.models`（过渡期，模型待归位）、
  `hunter1.slices.jobs`（公开面）—— 记录投递要按 id 查岗位；`applications → jobs`
  是依赖白名单允许的方向。

## 文件

| 文件 | 职责 |
|---|---|
| `router.py` | HTTP 端点（APIRouter 工厂） |
| `schemas.py` | API 模型（Pydantic，契约唯一事实来源） |
| `service.py` | 用例编排（记录投递、推进阶段） |
| `store.py` | 持久化门面 |

## API 面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/applications` | 投递列表（按 `updated_at` 倒序，取最近 200 条） |
| POST | `/api/applications` | 记录投递（201；body `{job_id}`，支持唯一前缀；歧义 → 409、不存在 → 404；**幂等**） |
| POST | `/api/applications/{application_id}/stage` | 推进阶段（200；不存在 → 404；非法 stage → 422） |
| DELETE | `/api/applications/{application_id}` | 删除投递（204；不存在为 no-op） |

## 独立验证命令

```bash
cd backend && <python> -m pytest tests/slices/applications -q
```
