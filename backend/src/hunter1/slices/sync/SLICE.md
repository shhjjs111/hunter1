# SLICE: sync

> 切片自述 —— 一个 Agent 只读本文件 + 根 `AGENTS.md`，即可合规地改这个切片。

## 职责（一句话）

在手机独立版与电脑端之间搬用户自己的求职数据（岗位 / 投递 / 候选人画像），
按时间戳解决冲突；不搬 API Key 与助手会话。

## 公开面（其他切片只能从这里 import）

| 符号 | 用途 |
|---|---|
| `SyncStore` | 快照导出 / 合并（只经 jobs / applications / scoring 的公开门面读写） |
| `build_router` | HTTP 面工厂；组装处注入 store |
| `SyncSnapshot` 等 | API 模型（契约源头 → `contracts/openapi.json`） |

## 冲突规则

- 岗位事实：`last_seen_at` 较新的一方胜；
- 岗位评分：`scored_at` 较新的一方胜，没有评分不覆盖已有评分；
- 投递：`updated_at` 较新的一方胜；
- 候选人画像：`updated_at` 较新的一方胜。

同步是合并而不是镜像：不传播删除（没有墓碑表），只在两端新增/更新记录。

跨设备稳定的身份是归一化后的详情 URL：

- 岗位按 `detail_url` 对齐；
- 投递按 `job_detail_url` 对齐。

两端各自抓同一岗位会生成不同 UUID，因此不能按 UUID 合并。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/sync/snapshot` | 导出完整快照 |
| POST | `/api/sync/merge` | 合并对端快照，返回计数与合并后的快照 |

## 依赖

`sync → jobs / applications / scoring`（在 `tests/test_architecture.py` 的
`SLICE_DEPENDENCY_ALLOW` 中显式登记）。只经公开面读写，不深链内部模块。

## 安全

端点本身不额外做鉴权：默认回环访问不需要口令；`--lan` 时由
`platform.access.AccessGateMiddleware` 统一拦截。安卓 App 用
`X-Hunter1-Access-Code` 请求头携带访问码（与浏览器 Cookie 同一口令）。
