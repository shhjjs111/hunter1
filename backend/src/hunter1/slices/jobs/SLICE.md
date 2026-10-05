# SLICE: jobs

> 切片自述 —— 一个 Agent 只读本文件 + 根 `AGENTS.md`，即可合规地改这个切片。

## 职责（一句话）

岗位与公司的事实（列表 / 搜索 / 详情）与**投递入口**（`POST /api/jobs/{id}/apply`）。

## 公开面（其他切片只能从这里 import）

| 符号 | 用途 |
|---|---|
| `JobStore` | 数据存取门面（唯一接触数据库的地方） |
| `build_router` | HTTP 面工厂；组装处注入 store 与 clock |
| `schemas` | API 模型（契约源头 → `contracts/openapi.json`） |
| `service` | 用例函数：`list_jobs` / `find_job` / `apply_to_job` |

## 依赖

- 允许：`hunter1.platform.*`（机制内核）、`hunter1.domain.models`（过渡期，模型待归位）
- 过渡登记（Wave 4 清除）：`hunter1.application.applications.new_application`
  （投递记录本体的归属是 applications 切片；入口先落在本切片）

## 文件

| 文件 | 职责 |
|---|---|
| `router.py` | HTTP 端点（APIRouter 工厂） |
| `schemas.py` | API 模型（Pydantic，契约唯一事实来源） |
| `service.py` | 用例编排（分页计算、前缀查找、投递） |
| `store.py` | 持久化门面 |

## API 面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/jobs?q=&page=&page_size=` | 列表/搜索（页码与页大小有界） |
| GET | `/api/jobs/{job_id}` | 详情（全 id 或唯一前缀；前缀歧义 → 409） |
| POST | `/api/jobs/{job_id}/apply` | 记录投递（201；落库后返回 application_id） |

## 独立验证命令

```bash
cd backend && <python> -m pytest tests/slices/jobs -q
```

## 迁移注（Wave 4 完成时删除本段）

- 表定义 `JobRow` / `CompanyRow` 与仓储 `SqliteJobRepository` 现居
  `platform/db/`，将归位到本切片（`store.py`）；
- `Job` / `Company` 模型现居 `hunter1.domain.models`，将归位到本切片；
- `new_application` 依赖将切换到 applications 切片公开面。
