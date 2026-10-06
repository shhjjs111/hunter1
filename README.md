# Hunter1

> 求职工作台 · 轻量 · 跨平台 · 零托管 · **前后端完全分离**

填入**自己的大模型 API Key**（任意 OpenAI 兼容厂商）即可使用：在本地完成
「岗位抓取 → 匹配评分 → AI 求职助手 → 投递管理」全流程。

## 架构一览

**后端**：切片化的纯 API（FastAPI + SQLite）。
**前端**：独立的 React SPA（Vite + TypeScript）。
**两者只经 OpenAPI 契约对话**：改后端模型 → 生成契约 → 前端类型自动跟进。

```
backend/src/hunter1/
├── platform/   机制内核（db / llm / fetch / update）
├── slices/     业务切片（jobs crawl applications assistant scoring settings）
└── main.py     组装根
frontend/src/
├── features/   ★ 与后端切片同名（认知对称）
└── shared/     设计系统 / 契约客户端 / SSE 原语
contracts/      OpenAPI 快照（生成物）
```

设计公理、依赖方向、已知取舍见 **docs/ARCHITECTURE.md**；
协作规则（多 Agent 并行开发）见 **AGENTS.md**。

## 快速开始

### 开发（双进程，前后端完全分离）

```bash
cd backend && pip install -e ".[dev,web,db,crawler]" && cd ..
cd frontend && npm install && cd ..
bash scripts/dev.sh          # 起 API(:8000) + Vite(:5173)
# 浏览器访问 http://127.0.0.1:5173
```

首次使用：到「配置」页填 base_url / 模型 / API Key（点「测试连接」验证），
再到「抓取」页跑一轮，岗位库就有数据了。

### 交付（单进程单目录）

```bash
./.tools/python/python.exe scripts/build.py --zip
```

产出 `dist/hunter1/`（exe + `_internal/`，含前端产物）与 `dist/hunter1-win32.zip`。
解压后直接运行，数据放在程序旁的 `data/`：

```
hunter1/hunter1.exe
hunter1/_internal/
hunter1/data/          ← 首次运行自动创建
```

构建脚本会做硬校验：**前端产物没打进去就判不合格** —— 那种包能启动，
但界面打不开（只剩裸 API）。

## 命令

| 目的 | 命令 |
|---|---|
| 全量门禁 | `bash scripts/check.sh` |
| 契约导出 / 漂移检查 | `bash scripts/contracts.sh` / `--check` |
| 开发双进程 | `bash scripts/dev.sh` |
| 后端测试 | `cd backend && <python> -m pytest` |
| 单切片测试 | `cd backend && <python> -m pytest tests/slices/jobs` |
| 前端检查 | `cd frontend && npm run check` |
| 命令行抓取 | `<python> scripts/serve.py` 之外的 `hunter1 crawl` |
| 自更新 | `hunter1 update --source <版本清单 URL>` |

## 现状

| 维度 | 状态 |
|---|---|
| 后端切片 | ✅ 6 个（jobs / crawl / applications / assistant / scoring / settings） |
| 前端 SPA | ✅ 5 个页面（岗位库 / 抓取 / 投递 / 助手 / 配置） |
| 契约流水线 | ✅ 导出 + 双漂移门禁（快照 + 前端类型） |
| 测试 | 后端 668 + 前端 40（含整体渲染验收） |
| 打包分发 | ✅ 单目录产物，前端产物嵌入 |

## 务实说明

- **抓取的是公开列表页**，不做登录、不绕验证码、**不做自动投递**。
  站点若返回风控页，会当作**显式失败**报出来，而不是静默给一个空列表。
- **API Key 明文存在本地 SQLite**：单机单用户场景下，系统钥匙串方案会引入
  平台特有依赖、与「零托管」冲突。界面只回显掩码，日志不输出完整密钥。
- **只绑本机**：默认监听 `127.0.0.1`。`--host 0.0.0.0` 会把无鉴权的界面
  暴露到局域网/公网，除临时演示外不要这么做。
- **助手只读**：它查岗位与投递、给建议；写操作由你在界面上确认。
- **无 no-JS 回退**：SPA 的取舍。旧版本（服务端渲染）有表单回退，v2 放弃了 ——
  换来的是前后端完全分离与更清晰的迭代边界。

## 与旧系统的关系

Hunter1 是对旧项目 `RecruitOps` 的重做：**迁移抓取资产与数据，替换外壳与运行时**。

- ✅ 继承：站点适配器思路、抓取资源治理、领域模型、匹配评分逻辑、助手工具面设计
- ❌ 丢弃：Electron 壳、PostgreSQL、Chromium 三重运行时（约 1.5GB）
- 🔄 替换：codex 求职助手（297MB 二进制、硬绑 DeepSeek）→ **自研轻量 agent**（任意 API 可用）

## 文档

- 📐 **[架构 →](docs/ARCHITECTURE.md)** ← 设计公理、依赖方向、已知取舍
- 🤖 **[协作宪法 →](AGENTS.md)** ← 切片地图、契约规则、门禁矩阵
- 🛠 **[开发指引 →](docs/DEVELOPMENT.md)** ← 环境、命令、打包细节
- 📄 **[历史规划 →](docs/DEVELOPMENT-PLAN.md)** ← v1 基线（已被 v2 取代）
