# SLICE: applications

> 切片自述 —— 一个 Agent 只读本文件 + 根 `AGENTS.md`，即可合规地改这个切片。

## 职责（一句话）

投递记录的事实（列表 / 阶段推进 / 删除）—— 「我做过的事」的完整生命周期。

## 公开面（其他切片只能从这里 import）

| 符号 | 用途 |
|---|---|
| `ApplicationStore` | 数据存取门面（唯一接触数据库的地方） |
| `build_router` | HTTP 面工厂；组装处注入 store 与 clock |
| `schemas` | API 模型（契约源头 → `contracts/openapi.json`） |
| `service` | 用例函数：`new_application` / `change_stage` |

## 依赖

- 允许：`hunter1.platform.*`（机制内核）、`hunter1.domain.models`（过渡期，模型待归位）

## 文件

| 文件 | 职责 |
|---|---|
| `router.py` | HTTP 端点（APIRouter 工厂） |
| `schemas.py` | API 模型（Pydantic，契约唯一事实来源） |
| `service.py` | 用例编排（建立投递、推进阶段） |
| `store.py` | 持久化门面 |

## API 面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/applications` | 投递列表（按 `updated_at` 倒序，取最近 200 条） |
| POST | `/api/applications/{application_id}/stage` | 推进阶段（200；不存在 → 404；未知 stage → 400） |
| DELETE | `/api/applications/{application_id}` | 删除投递（204；不存在为 no-op） |

## 独立验证命令

```bash
cd backend && <python> -m pytest tests/slices/applications -q
```

## 迁移注（Wave 4 完成时删除本段）

- 表定义 `ApplicationRow` 与仓储 `SqliteApplicationRepository` 现居
  `platform/db/`，将归位到本切片（`store.py`）；
- `Application` / `ApplicationStage` 模型现居 `hunter1.domain.models`，将归位到本切片；
- `new_application` / `change_stage` 现由 `application/applications.py` 承载
  （本切片已逐字迁入），旧模块在 jobs 入口切换后下线。
