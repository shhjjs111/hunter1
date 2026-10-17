# GitHub 仓库治理

Hunter1 的远端仓库按以下标准维护。目标不是增加流程装饰，而是让每次变更都能
追溯到提交、通过同一套检查、并留下可复现的发布记录。

## 主分支

`main` 使用 active branch ruleset：

- 只接受 Pull Request，禁止直接 push。
- 必需状态检查：`quality (3.12)`、`Analyze (python)`、
  `Analyze (javascript-typescript)`、`Review dependency changes`。
- 要求分支在合并前与 `main` 同步。
- 要求线性历史，禁止 force-push 与删除分支。
- 合并方式只开放 squash merge；合并后自动删除远端分支。

PR 标题遵循 Conventional Commits。一个 PR 只包含一个逻辑单元，验证证据使用真实
命令输出，不写推测性结论。

## 发布与 Tag

- 版本号遵循 SemVer，并先更新 `backend/src/hunter1/__init__.py` 与
  `CHANGELOG.md`。
- Release tag 使用 annotated tag `vX.Y.Z`，指向待发布的 `main` 提交。
- `v*` tag 受 active tag ruleset 保护，禁止删除和移动。
- 已启用 immutable releases；正式发布后不得替换资产或改写说明。
- Release 必须包含平台产物与 `manifest.json`，说明与 `CHANGELOG.md` 对应。
- 已发布版本不移动 tag；修复通过新版本发布。

## CI 与供应链

- `quality (3.12)` 在 Windows runner 上执行完整门禁，包括打包与冒烟。
- CodeQL 对 Python 与 JavaScript/TypeScript 做安全扫描。
- Dependency graph 为 Dependency Review 提供依赖差异；Dependency Review 拦截
  高风险依赖变更。
- Dependabot 每周更新 Python、npm 与 GitHub Actions 依赖。
- 第三方 action 必须固定到完整 40 位 commit SHA，由测试门禁检查。
- Actions 只允许 GitHub 官方 action，并强制 SHA 固定；默认 token 仅有只读权限。

## 安全

- 启用私有漏洞报告、Dependabot alerts、Dependabot security updates、
  secret scanning 与 push protection。
- 安全问题走 `SECURITY.md` 的私有通道，不在公开 Issue 中披露细节。
- 发布物必须经过仓库卫生门禁，运行数据、依赖目录、缓存、密钥形态与本机路径
  不得进入索引。

## 协作表面

- Issue 使用结构化模板；安全漏洞禁用公开 Issue 通道。
- PR 使用统一模板，列出动机、改动、验证、设计取舍与自查。
- 社区行为遵循 `CODE_OF_CONDUCT.md`。
- Wiki 与 Discussions 不启用；文档统一进入 `docs/`，避免信息分裂。
