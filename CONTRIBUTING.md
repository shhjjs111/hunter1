# 贡献指南

感谢你考虑为 Hunter1 贡献。本文档说明在本仓库提交变更的完整流程与硬性约束。

> 如果你（或你使用的 AI agent）要改代码，**必须**先读
> AGENTS.md —— 那是本仓库的协作宪法，切片所有权与契约纪律的
> 唯一权威来源。本文件是面向人类贡献者的入口，与之冲突时以 `AGENTS.md` 为准。

## 环境准备

需要 **Python ≥ 3.12**、**Node.js 24** 与 **uv 0.12.23**。

```bash
# 后端（必须消费仓库锁文件）
python -m pip install uv==0.12.23
uv sync --project backend --frozen \
  --extra dev --extra web --extra db --extra crawler --extra build

# 前端
cd frontend && npm ci && cd ..
```

Windows / Git Bash 用户可直接用仓库自带的解释器 `./.tools/python/python.exe`，
`scripts/check.sh` 会自动探测，无需手传。

`uv` 版本是精确锁定的：`[tool.uv] required-version` 会拒绝 `0.13.x` 等非
`0.12.23` 版本，这是为了确保锁文件解析结果可复现。升级 uv 时需同步修改
`backend/pyproject.toml`、`backend/uv.lock` 与 CI workflow。

历史卫生测试需要完整 Git 历史。若使用了浅克隆，请先在仓库根执行
`git fetch --unshallow`；CI 已通过 `fetch-depth: 0` 获取完整历史。

细节（命令矩阵、打包、常见坑）见 docs/DEVELOPMENT.md。

## 开工前

- **读架构**：设计公理、依赖方向、已知取舍见 docs/ARCHITECTURE.md。
- **看切片**：目标切片只读 `backend/src/hunter1/slices/<name>/SLICE.md`，
  即可知道其职责与对外 API 面。

## 硬性约束

以下几条由测试与 CI 钉死，违反即红灯：

1. **切片领地**：`backend/src/hunter1/slices/<name>/` 的文件只归属该切片。
   切片间只能经公开面引用（`from hunter1.slices.<name> import <符号>`），
   **禁止深链内部模块**。
2. **依赖方向**：`platform` ← `slices`；`slices` 之间只允许
   `crawl/scoring/applications/assistant → jobs`。由
   `backend/tests/test_architecture.py` 强制。
3. **契约先行**：后端 Pydantic 模型是 API 形状的唯一事实来源。**改了 API 就要跑**
   `bash scripts/contracts.sh` 重新导出快照与前端类型，**并把快照一起提交**。
   漂移由 `bash scripts/contracts.sh --check` 拦截。
4. **响应要有类型**：路由不要返回裸 `dict` —— 那会让 OpenAPI 退化成 `unknown`，
   前端拿不到类型。
5. **结构搬运与行为改动永不混在同一提交**。

## 提交前

**全量门禁必须全绿**（CI 跑的是同一个脚本，不可能漂移）：

```bash
bash scripts/check.sh
```

它会按目录存在性自动纳入：后端 `ruff format` + `ruff check` + `pyright` + `pytest`、
前端 `npm run check`、契约漂移门禁、以及打包冒烟（`--with-pack` 时）。

单跑某一类检查的命令见 `scripts/check.sh` 内部分节与
docs/DEVELOPMENT.md。

## 提交信息

遵循 Conventional Commits：

```
<type>(<scope>): <说明>
```

`type` 取 `feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf`。

- **一个逻辑单元一次提交**，不攒批。
- 说明写**为什么**，而不只是**改了什么** —— 本仓库的提交历史与代码注释都
  遵循这个习惯（例：`fix(platform/fetch): 被跟随的重定向不再读响应体，避免误报 too_large`）。

## 提交变更

仓库采用受保护主分支与 Pull Request 流程。`main` 上的提交必须来自 PR，
并通过 `quality (3.12)` 门禁。

1. Fork 仓库，从 `main` 建分支；分支名使用
   `feat/<topic>`、`fix/<topic>`、`docs/<topic>`、`test/<topic>` 等前缀。
2. 改代码 + 补测试。**新行为与缺陷修复都要有测试**；缺陷修复的测试应当
   **先能复现原缺陷**（RED），改完转绿（GREEN）。
3. 跑 `bash scripts/check.sh` 至全绿。
4. 开 Pull Request，标题使用 Conventional Commits，说明动机、改动点、验证方式；
   关联相关 Issue。
5. 等待 `quality (3.12)`、CodeQL 与 Dependency Review 完成。维护者使用 squash
   merge 合并，远端分支自动删除。

修改 Python 依赖时，必须同时提交重新生成的 `backend/uv.lock`：

```bash
uv lock --project backend --python 3.12
```

Release 由 `v*` tag 触发 GitHub Actions，不在开发机手工上传发布资产。

直接向 `main` push、force-push、删除受保护 tag 都会被 GitHub 规则集拒绝。

## 报告问题

- **安全漏洞**：**不要**开公开 Issue，走 SECURITY.md 的私有通道。
- **功能与缺陷**：用仓库的 Issue 模板。

## 许可

向本仓库提交贡献，即表示你同意你的贡献按本仓库的
[MIT License](LICENSE) 授权。
