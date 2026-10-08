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

## 抓取礼貌（装配决定，切片里没有开关）

「对站点礼貌」的三个机制实现在 `platform/fetch`，**平台层的默认值是关的**
（保测试确定性：不然每个用例都要等间隔、每个主机都要多打一次 robots.txt），
由组装根 `main.AppContext.default` 打开：

| 机制 | 装配取值 | 为什么 |
|---|---|---|
| 每主机请求间隔 | `min_interval=1.0` | UA 轮换 + 自动重试会放大请求密度 |
| 遵守 `robots.txt` | `respect_robots=True` | 站点明确禁止 → `FetchError("robots_disallowed")`，不重试、不计主机故障 |
| 单响应体积上限 | `max_bytes`（默认 8MB，**始终开**） | 重定向落到大文件会吃满内存；超限报 `too_large`，刻意**不静默截断**（半截 HTML 会产出看起来正常的残缺列表） |

加站点或改站点时别绕开 `HttpFetcher`（例如直接 `httpx.get`）—— 那会把这三条一起丢掉。

## 独立验证命令

```bash
cd backend && <python> -m pytest tests/slices/crawl -q
```

## 设计取舍（记录用）

- **未做 SSE 进度推流**：保留轮询语义（与旧界面 `setInterval` 对快照的用法一致）。
  改成服务端推流是独立改进，不在结构迁移范围内。
- `JobRepository` 目前由 `platform.db` 提供；jobs 切片将来若自带 store，
  可改为经其公开面注入（协议不变，只换实现）。
