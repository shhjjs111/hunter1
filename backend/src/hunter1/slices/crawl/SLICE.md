# SLICE: crawl

> 切片自述 —— 一个 Agent 只读本文件 + 根 `AGENTS.md`，即可合规地改这个切片。

## 职责（一句话）

把招聘站点的公开列表页翻成岗位事实并落库；对外提供「启动一轮 / 查进度」两个端点。

## 公开面（其他切片只能从这里 import）

| 符号 | 用途 |
|---|---|
| `crawl_company` / `crawl_all` | 抓取用例（编排 + 落库） |
| `CrawlResult` / `BatchCrawlResult` | 结果模型（失败收进 `.error`，不抛出） |
| `CrawlRunner` / `CrawlSnapshot` / `SiteProgress` | 进度运行器（后台线程 + 快照） |
| `SITES` / `SiteDefinition` / `available_sites` / `build_site` / `build_all` / `get_site` | 站点注册表 |
| `ListPageSpec` / `StaticHtmlCrawler` / `BaseCrawler` / `parse_list_page` | 适配器 |
| `CrawlBlockedError` / `detect_blocking` / `ensure_not_blocked` | 风控页守卫 |
| `build_router` | HTTP 面工厂 |

## 依赖

- 允许：`hunter1.platform.*`、`hunter1.domain.*`、`hunter1.application.ports`（协议）
- 无出边到其他切片（jobs 的落库经 `JobRepository` 协议注入，不是 import）

## 文件

| 文件 | 职责 |
|---|---|
| `router.py` | HTTP 端点（`POST /api/crawl`、`GET /api/crawl/status`） |
| `service.py` | 抓取编排（`crawl_company` / `crawl_all`、合并不变量） |
| `adapters.py` | 基础爬虫 + 声明式列表页适配器（原 base + static_html 合并） |
| `sites.py` | 站点注册表（6 个真实站点） |
| `guards.py` | 风控页识别（「被拦截」≠「没有岗位」） |
| `runner.py` | 后台线程运行器 + 进度快照 |

## API 面

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/crawl` | 启动一轮；`{started: bool}`（已在跑则为 false，不报错） |
| GET | `/api/crawl/status` | 进度快照（`running`/`sites`/`total_fetched`…） |

## 独立验证命令

```bash
cd backend && <python> -m pytest tests/slices/crawl -q
```

## 设计取舍（记录用）

- **未做 SSE 进度推流**：保留轮询语义（与旧界面 `setInterval` 对快照的用法一致）。
  改成服务端推流是独立改进，不在结构迁移范围内。
- `JobRepository` 目前由 `platform.db` 提供；jobs 切片将来若自带 store，
  可改为经其公开面注入（协议不变，只换实现）。
