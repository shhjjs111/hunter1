# SLICE: jobs

> 切片自述 —— 一个 Agent 只读本文件 + 根 `AGENTS.md`，即可合规地改这个切片。

## 职责（一句话）

岗位与公司的事实（列表 / 搜索 / 详情）。投递入口不在这里 —— 投递记录的本体归
applications 切片，记录投递的端点随之归它（`POST /api/applications`）。

## 公开面（其他切片只能从这里 import）

| 符号 | 用途 |
|---|---|
| `JobStore` | 数据存取门面（唯一接触数据库的地方） |
| `build_router` | HTTP 面工厂；组装处注入 store（**不注入时钟** —— 本切片只剩只读端点） |
| `schemas` | API 模型（契约源头 → `contracts/openapi.json`） |
| `service` | 用例函数：`list_jobs` / `find_job` |

## 依赖

- 允许：`hunter1.platform.*`（机制内核）、`hunter1.domain.models`（过渡期，模型待归位）

## 文件

| 文件 | 职责 |
|---|---|
| `router.py` | HTTP 端点（APIRouter 工厂） |
| `schemas.py` | API 模型（Pydantic，契约唯一事实来源） |
| `service.py` | 用例编排（分页计算、前缀查找） |
| `store.py` | 持久化门面 |

## API 面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/jobs?q=&page=&page_size=` | 列表/搜索（`page` ≤ `MAX_PAGE=10000`、`page_size` ≤ `MAX_PAGE_SIZE=100`，**越界由 422 拒绝**） |
| GET | `/api/jobs/{job_id}` | 详情（全 id 或唯一前缀；前缀歧义 → 409） |

**分页上界在边界处拒绝，不静默钳制**：`page` 与服务层 `min(max(1, page), MAX_PAGE)`
两道口径一致 —— 传入 999999 会得到 422（而不是「200 但悄悄返回第 10000 页」）。
前缀歧义的 409 文案说「**至少** N 条匹配」：N 是带上限的计数，不是精确总数。

## 独立验证命令

```bash
cd backend && <python> -m pytest tests/slices/jobs -q
```
