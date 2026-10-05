# AGENTS.md — 协作宪法

> **验收定义**：一个 Agent 只读 **本文件 + 目标切片的 SLICE.md + contracts/**，
> 即可提交合规变更。所有协作规则从这一句导出。

## 仓库地图（monorepo）

```
hunter1/
├── backend/          Python API 服务（FastAPI + SQLite）
│   ├── src/hunter1/
│   │   ├── platform/   机制内核（零业务：db / llm / fetch / update / text）
│   │   ├── slices/     业务垂直切片（jobs / crawl / applications / assistant / scoring / settings）
│   │   ├── domain/     共享模型（过渡期，见 docs/ARCHITECTURE.md 的技术债）
│   │   ├── application/ 端口协议（进程边界）+ 并存期残留
│   │   ├── main.py     组装根 —— 唯一认识所有切片的地方
│   │   └── cli.py      serve / crawl / update
│   └── tests/          镜像 src 结构；tests/slices/<name> = 该切片的独立验证
├── frontend/         React SPA（独立工程；features/ 与后端 slices/ 同名）
├── contracts/        OpenAPI 契约快照（生成物，禁手改）
├── scripts/          跨端工程脚本（check.sh / dev.sh / contracts.sh / build.py）
└── docs/             架构白皮书与开发指引
```

## 切片所有权（Agent 并行开发的核心）

- **一个切片 = 一个领地**：`backend/src/hunter1/slices/<name>/` 与
  `frontend/src/features/<name>/` 的文件只归属该切片。
- **切片间只经公开面**：只允许 `from hunter1.slices.<name> import <符号>`
  （即该切片 `__init__.py` 导出的公开面）。禁止深链内部模块
  （如 `...slices.jobs.store`）—— 深链让领地边界失效，并行改动的冲突会回来。
- **依赖方向白名单**（架构测试钉死，越权即红灯）：
  `platform` ← `slices`；`slices` 之间只允许 `crawl/scoring/applications/assistant → jobs`。
  进程边界协议从 `hunter1.application.ports` 取（显式豁免，见架构测试的
  `SHARED_PORT_MODULE`）。
- **同切片内串行，跨切片并行。**

## 契约先行（跨端协作的唯一通道）

1. 后端 Pydantic 模型（`slices/*/schemas.py`）是 **API 形状的唯一事实来源**；
2. 改 API → 跑 `bash scripts/contracts.sh` 重新导出快照，**连快照一起提交**；
3. 前端类型从 `contracts/openapi.json` 生成（`schema.d.ts`，**禁手改**）；
4. 漂移由 `bash scripts/contracts.sh --check` 拦截（进 CI 门禁）。
5. **响应要有类型**：路由返回裸 `dict` 会让 OpenAPI 退化成 `unknown`，
   前端拿不到类型 —— 契约在最有价值的地方断掉（有前例，见 crawl 切片的注释）。

## 门禁命令矩阵

| 目的 | 命令 |
|---|---|
| 全量门禁（提交前必过） | `bash scripts/check.sh` |
| 后端切片独立验证 | `cd backend && <python> -m pytest tests/slices/<name> -o addopts=` |
| 后端全量 | `cd backend && <python> -m pytest -o addopts=` |
| 后端类型检查 | `cd backend && <python> -m pyright --pythonpath <python>` |
| 契约漂移门禁 | `bash scripts/contracts.sh --check` |
| 前端检查 | `cd frontend && npm run check` |
| 开发形态（双进程） | `bash scripts/dev.sh` |
| 打包 + 冒烟 | `<python> scripts/build.py` |

> 本机（Windows/Git Bash）解释器为 `./.tools/python/python.exe`。
> `scripts/check.sh` 自动探测解释器，无需手传。
> ⚠️ 后端 `pyproject.toml` 的 `addopts=-q` 与命令行 `-q` 叠加成 `-qq` 会吞掉
> 汇总行 —— 要读计数用 `-o addopts=` 覆盖。

## 新增一个切片（机械规程）

1. 建 `backend/src/hunter1/slices/<name>/`，形制：
   `__init__.py`（公开面）/ `router.py` / `schemas.py`（**有类型**）/
   `service.py` / `store.py` / `SLICE.md`
2. 在 `main.py` 挂载 router（`_mount(build_xxx_router(...))`）；
3. 更新 `backend/tests/test_architecture.py` 的依赖白名单（如需跨切片依赖）；
4. 写 `backend/tests/slices/<name>/` 测试（含 router 测试）；
5. `bash scripts/contracts.sh` 导出契约快照 + 前端类型；
6. 建同名前端 feature：`frontend/src/features/<name>/`（api.ts + 页面 + 组件测试）；
7. 在 `frontend/src/app/App.tsx` 的路由表加一条，`AppLayout.tsx` 的 NAV 加一项。

## 提交纪律

- 格式：`<type>(<scope>): <说明>`（feat/fix/refactor/docs/test/chore/perf）。
- **一个逻辑单元一次提交，不攒批**；结构搬运与行为改动**永不混在同一提交**。
- 提交前 `bash scripts/check.sh` 全绿。
- 报告里的每个数字必须来自本轮工具输出 —— 「改完了所以应该过」不是证据。

## 何时看哪份文档

| 想知道 | 看 |
|---|---|
| 架构全貌、设计公理、已知取舍 | `docs/ARCHITECTURE.md` |
| 环境搭建、命令细节、打包 | `docs/DEVELOPMENT.md` |
| 某个切片的职责与 API 面 | `backend/src/hunter1/slices/<name>/SLICE.md` |
| 契约纪律 | `contracts/README.md` |
| 历史规划（v1，已被 v2 取代） | `docs/DEVELOPMENT-PLAN.md` |
